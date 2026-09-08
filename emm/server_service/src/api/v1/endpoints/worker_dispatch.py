"""Worker-dispatch endpoints для maintenance/inventory/provision task'ов.

Дополняет `endpoints/ipmi.py` (power.on/off/reboot) и user-facing
`endpoints/server_accounts.py` (`/rotate_password` — локальная ротация без
SSH-apply). Дисптачи через `worker_client.dispatch_task`:

* ``POST /servers/{id}/power/status``      → `power.status`
  (live BMC-probe, требует IPMI-row).
* ``POST /servers/{id}/inventory/sync``    → `inventory.sync`
  (full SSH-probe: lscpu/lsblk/os-release).
* ``POST /servers/{id}/users/inventory``   → `users.inventory`
  (getent → reconcile в server_accounts; роут живёт в `endpoints/inventory.py`,
  здесь упомянут только для полноты карты диспатчей).
* ``POST /server-accounts/{id}/rotate``    → `account.rotate_password`
  (worker: generate → SSH chpasswd → submit ciphertext; точечная
  `?server_id=` либо массовый fan-out на все linked серверы).
* ``POST /server-accounts/{id}/provision`` → `account.provision`
  (useradd на боксе; запись `present_on_server=True`).
* ``POST /server-accounts/{id}/update_on_host`` → `account.update_on_host`
  (usermod атрибутов: sudo/groups/shell). Также вызывается fan-out'ом
  из PATCH аккаунта через `fanout_update_on_host`.
* ``POST /server-accounts/{id}/deprovision`` → `account.deprovision`
  (userdel; `present_on_server=False`).
* ``POST /servers/{id}/prepare``           → `server.prepare`
  (bootstrap управляющего юзера DBOS; bootstrap-креды кладутся в Redis
  под `bootstrap_creds_key` с TTL, в payload едет только ссылка).
* ``POST /servers/{id}/install-node-exporter`` → `server.install_node_exporter`
  (managed SSH: установить node_exporter для Grafana-метрик).
* ``POST /ipmi-controllers/{id}/rotate``   → `ipmi.rotate_password`
  (worker сейчас raise'ит NotImplementedError до того, как тронет iDRAC —
  storage round-trip ещё не существует. Endpoint всё равно поднимает таску,
  worker mark_failed + audit failure через `_runner` — это полный
  defense-in-depth контракт «вызов фиксируется до того, как handler
  откажет», см. server_worker/src/tasks/passwords.py SAFETY GUARD).

Дополнительно `installed_packages.list` диспатчится из
`endpoints/installed_packages.py` (live dpkg-query/rpm -qa через SSH без
записи в БД).

Общая схема (см. `_dispatch_power` в `endpoints/ipmi.py` как канонический
референс):

  1. Загрузка целевого ресурса с visibility-check (cross-dept → 404).
  2. `require_action` для конкретной (entity_type, action) пары.
  3. Бизнес-валидации (decomissioned-сервер, наличие IPMI-row там, где
     handler без BMC всё равно упадёт).
  4. `worker_client.dispatch_task(...)` с пробросом `Idempotency-Key`
     header'а и `target_department_id` в payload.
  5. Audit-emit на каждой ветке (denied/failure/success).

Любые `ConflictError(TASK_IDEMPOTENT_CONFLICT)` /
`ServiceUnavailableError(WORKER_*)` из `worker_client.dispatch_task`
сопровождаются explicit failure-emit'ом, иначе попытка остаётся в audit
только под generic `http.client_error`/`http.server_error` middleware'а.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import (
    AccountSource,
    Action,
    BusyActorType,
    BusyState,
    EntityType,
    SERVICE_RESERVATION_ACS,
    ServerStatus,
)
from src.core.limiter import endpoint_limiter, per_account_key
from src.core.exceptions import (
    AppException,
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.dependencies.idempotency import read_idempotency_key
from src.api.v1.endpoints._dispatch import (
    build_ssh_task_payload,
    dispatch_server_ssh_task,
)
from src.repositories import ipmi_controller as ipmi_repo
from src.repositories import os_version as os_version_repo
from src.repositories import server as server_repo
from src.repositories import server_account as account_repo
from src.repositories import vm as vm_repo
from src.schemas.server import (
    AcsAvailabilityResponse,
    AcsSnapshotItem,
    AcsSnapshotListResponse,
    ServerAcsSnapshotBatchRequest,
    ServerAcsSnapshotBatchResponse,
    ServerAcsSnapshotCreateRequest,
    ServerAcsSnapshotRestoreRequest,
    ServerAstraUpdateRequest,
    ServerBatchDispatched,
    ServerBatchFailed,
    ServerCleanActionResult,
    ServerCleanRequest,
    ServerCleanResponse,
    ServerManagementCredsRotateResponse,
    ServerOsVersionUpdate,
    ServerPowerStatusDispatchResponse,
    ServerPrepareBatchRequest,
    ServerPrepareBatchResponse,
    ServerPrepareBulkRequest,
    ServerPrepareBulkResponse,
    ServerPrepareBulkResult,
    ServerPrepareRequest,
    ServerPrepareResponse,
    ServerTaskDispatchResponse,
)
from src.schemas.server_account import (
    AccountProvisionDispatchResponse,
    AccountRotateDispatchResponse,
    AccountRotateSkipped,
    AccountRotateTask,
    AccountVmProvisionDispatchResponse,
)
from src.services import (
    acs_client,
    acs_settings as acs_settings_svc,
    audit_service,
    management_creds as management_creds_svc,
    management_user_config as management_user_config_svc,
    os_version_bootstrap_password as bootstrap_password_svc,
    permissions,
    reservation,
    server_account as account_svc,
    worker_client,
)
from src.services import server as server_svc
from src.services import vm as vm_svc
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import dispatch_creds_id, prepare_creds_id, rotation_batch_id

logger = logging.getLogger(__name__)

router_servers = APIRouter(prefix="/servers/{server_id}")
router_accounts = APIRouter(prefix="/server-accounts/{account_id}")
router_ipmi = APIRouter(prefix="/ipmi-controllers/{controller_id}")


# ── helpers ─────────────────────────────────────────────────────────────────


def _build_account_task_payload(
    *,
    server,
    account,
    include_attrs: bool,
    include_home_dir: bool | None = None,
) -> dict:
    """Базовый payload для account-task'ов (rotate / provision / update / deprovision).

    Поля одинаковые для всех аккаунт-task'ов: ключи маршрутизации (host/ssh_port),
    identity аккаунта (login), management-режим сервера. `include_attrs=True`
    добавляет управляемые атрибуты (`has_sudo`/`unix_groups`/`shell`), которые
    нужны useradd/usermod (`account.provision`/`update_on_host`/`deprovision`);
    для `account.rotate_password` атрибуты не нужны (там только меняется пароль
    через chpasswd).

    `home_dir` едет только в `provision` — `useradd -d <path>` ставит домашний
    каталог при заведении пользователя. `usermod` в `modify_user` (worker'ский
    `account.update_on_host`) `-d` не передаёт: смена home существующего юзера —
    отдельный сценарий с переносом данных, через PATCH сейчас не делается.
    `deprovision` — `userdel`, home в payload'е тоже не нужен.

    По умолчанию (`include_home_dir=None`) home_dir едет, если `include_attrs=True`,
    чтобы не ломать существующих caller'ов; provision-вызов берёт дефолт,
    update/deprovision передают `False` явно.

    Caller дополняет результат своими ключами (`extra_payload`) через `.update`.
    """
    payload: dict = {
        "server_id": server.id,
        "account_id": account.id,
        "target_department_id": server.department_id,
        # Адресация по SSH: ключи `host`/`ssh_port` читает воркер в
        # ssh_client._extract_host / _extract_port. В host кладём IP, а не
        # hostname — короткие имена не резолвятся из пода воркера (resolv.conf
        # только с k8s CoreDNS), IP достижим без резолва. Без host воркер
        # фоллбэчится на server_id (UUID) и вовсе рвёт подключение.
        "host": str(server.ip_address),
        "ssh_port": server.ssh_port,
        "login": account.login,
        # На подготовленном сервере worker заходит под управляющим пользователем
        # по ключу с sudo, а не self-сессией под аккаунтом.
        "is_managed": server.is_managed,
        "management_user": server.management_user,
    }
    if include_attrs:
        payload["has_sudo"] = account.has_sudo
        payload["unix_groups"] = list(account.unix_groups)
        payload["shell"] = account.shell
        if include_home_dir is None or include_home_dir:
            payload["home_dir"] = account.home_dir
    return payload


def _server_name(server) -> str:
    """Человекочитаемое имя сервера для per-task деталей в ответе.

    display_name, если задан, иначе hostname — то же правило, что в карточке
    сервера.
    """
    return server.display_name or server.hostname


def require_server_prepared(server, *, audit_action: str) -> None:
    """Гейт «инвентаризация только после prepare».

    Действия инвентаризации (`inventory.sync`, `users.inventory`,
    `installed_packages.list`) ходят на сервер по SSH под управляющим
    пользователем DBOS — вход по ключу, заведённому prepare-циклом. До
    prepare ключа нет, заходить нечем: раньше worker фоллбэчился на пароль
    привязанного аккаунта, теперь этот путь снят. Неподготовленный сервер
    отбиваем 409 `PREPARE_REQUIRED` с failure-аудитом, чтобы оператор сначала
    прогнал prepare.
    """
    if server.is_managed:
        return
    audit_service.emit(
        audit_action, target_id=server.id, target_type="server",
        status="failure", allowed=True,
        details={
            "reason": "prepare_required",
            "department_id": server.department_id,
        },
    )
    raise ConflictError(
        error_code="PREPARE_REQUIRED",
        message=(
            "Server is not prepared for management; run POST "
            "/servers/{id}/prepare before inventory operations"
        ),
    )


async def _dispatch_for_server(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    server_id: str,
    action: str,
    audit_action: str,
    task_kind: str,
    require_ipmi: bool,
    require_prepared: bool = False,
    extra_payload: dict | None = None,
) -> dict:
    """Общая логика server-target dispatch'а (power.status / inventory.sync).

    Порядок check'ов — те же приоритеты, что у `_dispatch_power` в
    `endpoints/ipmi.py`:

      1. ``require_action(action)`` — для конкретной операции. Идёт первой
         по канону permission → visibility: проверка зависит только от ролей
         caller'а, на target_id не смотрит — 403 на этом шаге не делает
         existence-oracle.
      2. ``server_svc.get_server`` (VIEW + dept-isolation) — 404 cross-dept.
      3. ``SERVER_DECOMMISSIONED`` — списанные сервера не принимают ни одной
         worker-операции.
      4. ``NO_IPMI_CONTROLLER`` (404) — для task'ов, которые ходят в BMC
         (power.status). Для inventory.sync — пропускаем: handler идёт по SSH.
         Унифицирован с `endpoints/ipmi.py::_dispatch_power` (тоже 404
         NO_IPMI_CONTROLLER), чтобы клиент не угадывал по коду, какой именно
         из IPMI-эндпоинтов он дёргает.
      4.5. ``PREPARE_REQUIRED`` (409) — для inventory-task'ов (`require_prepared`):
         сбор идёт по SSH под управляющим ключом, до prepare заходить нечем.
      5. ``worker_client.dispatch_task`` + audit-emit на каждой ветке.

    ``extra_payload`` мерджится поверх стандартного ``{server_id,
    target_department_id, host, ssh_port, is_managed, management_user}``
    — нужен, если в будущем появится task-kind с собственными полями.
    Сейчас оба call-site'а (`power.status`, `inventory.sync`) идут с
    `extra_payload=None`.
    """
    # 1. Role-check.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, action
        )

    # 2. Visibility + dept isolation.
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        # NotFoundError — visibility-404 (cross-dept / нет row): caller прошёл
        # permission, цель невидима → `failure`/`allowed=True`.
        # AuthorizationError — отказ VIEW (роль с конкретным action, но без
        # VIEW) → `denied`/`allowed=False`. Канон — `endpoints/ipmi.py::_dispatch_power`.
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise

    # 3. Decommissioned-gate.
    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept worker operations",
        )

    # 3.5. Гейт обновления ОС: пока сервер `updating`, никакие worker-операции
    # к нему не адресуем (даже power.status / inventory) — параллель посреди
    # astra-update опасна. Блокирует всех, включая владельца брони и админа.
    reservation.ensure_not_updating(identity, server, action=audit_action)
    # Гейт ACS: сервер занят снимком/восстановлением — до его окончания
    # worker-операции доступны только админу. Ставим рядом с гейтом updating.
    reservation.ensure_not_acs_locked(identity, server)

    # 4. IPMI-row gate для тех task-kinds, которые ходят в BMC.
    if require_ipmi:
        ipmi_ctrl = await ipmi_repo.get_by_server_id(db, server_id)
        if ipmi_ctrl is None:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "no_ipmi", "department_id": server.department_id},
            )
            raise NotFoundError(
                error_code="NO_IPMI_CONTROLLER",
                message="Server has no IPMI controller configured (BMC endpoint/credentials missing)",
            )

    # 4.5. Prepare-gate для inventory-task'ов. SSH-сбор идёт под управляющим
    # ключом (после prepare), self-сессия по паролю аккаунта снята —
    # неподготовленный сервер отбиваем 409 PREPARE_REQUIRED.
    if require_prepared:
        require_server_prepared(server, audit_action=audit_action)

    # 5. Dispatch + audit — общая обвязка в `_dispatch.dispatch_server_ssh_task`
    # (базовый payload + dispatch с target_resource_id + ConflictError/
    # ServiceUnavailableError-audit + commit + success-audit). На managed-сервере
    # worker заходит по ключу — резолвнутый аккаунт всегда None.
    task_id, _ = await dispatch_server_ssh_task(
        db=db, identity=identity, request=request,
        server=server,
        task_kind=task_kind,
        audit_action=audit_action,
        resolved_account_id=None,
        extra_payload=extra_payload,
    )
    return {"task_id": task_id, "status": "queued"}


def _decommissioned_account_dispatch_guard(
    *,
    server,
    account_id: str,
    audit_action: str,
    operation: str,
) -> None:
    """Отдельный decommissioned-gate для account-dispatch'ей.

    Вынесен из `_resolve_account_and_server`, потому что dispatch'ам, у которых
    есть `Idempotency-Key`, decommission-проверку надо делать ПОСЛЕ replay'я
    (иначе повторный POST после decommission'а отвечает 409 вместо existing
    task_id, retry-семантика ломается). Без ключа порядок прежний.
    """
    if server.status != ServerStatus.DECOMMISSIONED:
        return
    audit_service.emit(
        audit_action, target_id=account_id, target_type="server_account",
        status="failure", allowed=True,
        details={
            "reason": "decommissioned",
            "server_id": server.id,
            "operation": operation,
            "department_id": server.department_id,
        },
    )
    raise ConflictError(
        error_code="SERVER_DECOMMISSIONED",
        message="Server is decommissioned and cannot accept worker operations",
    )


async def _resolve_account_and_server(
    *,
    db: AsyncSession,
    identity,
    account_id: str,
    server_id: str,
    action: str,
    audit_action: str,
    operation: str,
    check_decommissioned: bool = True,
    acl_action: str | None = None,
):
    """Permission + visibility + dept-isolation для пары account+server.

    Возвращает `(account, server)`. На любом провале эмитит failure-audit и
    поднимает исключение.

    Авторизация аддитивная (роль ИЛИ per-account грант): сначала грузим учётку
    (per-account грант нельзя проверить, не зная конкретную учётку). Держатель
    бланкетной роли `action` ведёт себя как раньше — visibility-404 на
    невидимую цель. Без роли проходит только тот, у кого есть прямой грант
    `acl_action` на эту видимую учётку. `acl_action` по умолчанию совпадает с
    `action`; параметр оставлен на случай, когда ролевой и per-account ключи
    расходятся.

    `check_decommissioned=False` отключает финальный decommission-check —
    caller тогда отвечает за `_decommissioned_account_dispatch_guard` после
    своей idempotency-replay-ветки.
    """
    acl_action = acl_action or action
    has_role = await permissions.has_action(
        db, identity, EntityType.SERVER_ACCOUNT, action,
    )
    account = await account_repo.get_by_id(db, account_id)
    visible = account is not None and account.department_id == identity.department_id

    if has_role:
        if not visible:
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept", "operation": operation},
            )
            raise NotFoundError(
                error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
            )
    else:
        # Без бланкетной роли — единственный путь это прямой грант на видимую
        # учётку. Невидимая/чужая/без-гранта → одинаковый 403 (no oracle).
        if not (
            visible
            and await permissions.has_account_action(db, identity, account, acl_action)
        ):
            details = {
                "server_id": server_id,
                "operation": operation,
                "reason": "permission_denied",
            }
            if getattr(identity, "subject_type", None) is not None:
                details["subject_type"] = identity.subject_type
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="denied", allowed=False, details=details,
            )
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message=f"No access to action '{acl_action}' on this server account",
                details={"entity_type": "server_account", "action": acl_action},
            )

    if server_id not in account_repo.linked_server_ids(account):
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "server_not_linked",
                "server_id": server_id,
                "operation": operation,
            },
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )

    # `load_visible_server` поднимает `NotFoundError` если сервер не найден
    # или его department изменили out-of-band уже после линковки с аккаунтом
    # (cross-dept edge). Без явного failure-аудита эта ветка молча отдаёт 404,
    # security-trail теряет evidence — повторяем паттерн `server_account.get`.
    try:
        server = await server_svc.load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "server_not_found_or_cross_dept",
                "server_id": server_id,
                "operation": operation,
            },
        )
        raise
    if check_decommissioned:
        _decommissioned_account_dispatch_guard(
            server=server,
            account_id=account_id,
            audit_action=audit_action,
            operation=operation,
        )
    return account, server


async def _dispatch_account_on_host(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account_id: str,
    server_id: str,
    action: str,
    audit_action: str,
    task_kind: str,
    operation: str,
    extra_payload: dict | None = None,
    include_home_dir: bool | None = None,
    acl_action: str | None = None,
) -> dict:
    """Per-server update/deprovision OS-пользователя.

    Provision'у нужны inline-креды + savepoint вокруг dispatch'а, поэтому
    он живёт отдельно в `_dispatch_account_provision`. Update/deprovision
    плейн-payload без секретов, savepoint не нужен — на этой ветке только
    permission/visibility/dispatch.
    """
    account, server = await _resolve_account_and_server(
        db=db, identity=identity, account_id=account_id, server_id=server_id,
        action=action, audit_action=audit_action, operation=operation,
        acl_action=acl_action,
    )
    # Бронь: правка/снос OS-пользователя на хосте — деструктив. На занятом
    # чужим сервере разрешаем только владельцу брони или админу.
    reservation.ensure_not_reserved_for(identity, server, action=audit_action)
    reservation.ensure_not_acs_locked(identity, server)

    idempotency_key = read_idempotency_key(request)
    payload = _build_account_task_payload(
        server=server, account=account, include_attrs=True,
        include_home_dir=include_home_dir,
    )
    if extra_payload:
        payload.update(extra_payload)
    server_id_v = server.id
    server_dept_v = server.department_id
    account_login_v = account.login
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=task_kind,
            target_server_id=server_id_v,
            target_resource_id=account_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
        await db.commit()
    except ConflictError:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "idempotent_conflict",
                "task_kind": task_kind,
                "server_id": server_id_v,
                "operation": operation,
                "department_id": server_dept_v,
            },
        )
        raise
    except ServiceUnavailableError:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "worker_unreachable",
                "task_kind": task_kind,
                "server_id": server_id_v,
                "operation": operation,
                "department_id": server_dept_v,
            },
        )
        raise
    audit_service.emit(
        audit_action, target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": task_kind,
            "server_id": server_id_v,
            "operation": operation,
            "login": account_login_v,
            "department_id": server_dept_v,
            "idempotent_hit": idempotent_hit,
        },
    )
    return {"operation": operation, "server_id": server_id_v, "task_id": task_id, "status": "queued"}


async def _dispatch_account_provision(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account_id: str,
    server_id: str,
    audit_action: str,
    task_kind: str,
    force_password: bool,
) -> dict:
    """Provision dispatch с creds-stash, savepoint и pending_apply.

    Контракт:
    * Discovered + `password_encrypted IS NULL` + нет inline-пароля → 409
      `ACCOUNT_HAS_NO_PASSWORD` (fail-fast в dispatch'е до worker'а).
    * Discovered + `password_encrypted IS NULL` + `force_password=true` →
      сгенерить новый пароль + ssh-keypair, force_replace=true (chpasswd
      на боксе).
    * Managed (либо discovered с уже сохранённым ciphertext'ом) → sticky
      существующий пароль, force_replace только если pending_apply'нутый
      ciphertext ещё не подтверждён callback'ом worker'а (race-fix).
    * password + ssh_private_key уезжают в Redis-stash под
      `dbos:dispatch_creds:<dcd_id>` envelope-encrypted'ом (`encrypt_stash`,
      TTL = `dispatch_creds_ttl_seconds`), в task-payload едет только ссылка
      `creds_stash_key`. Симметрия с `server.prepare` (bootstrap_creds_key).
    """
    operation = "provision"
    # decommissioned-check и account_has_no_password откладываем — Idempotency-Key
    # replay должен отработать ДО них. Повторный POST с тем же ключом после
    # decommission'а / удаления пароля обязан вернуть existing task_id, иначе
    # клиент видит 409 на ретрае собственной успешной операции. Если ключа
    # нет — гарды ниже отбоят как раньше.
    account, server = await _resolve_account_and_server(
        db=db, identity=identity, account_id=account_id, server_id=server_id,
        action=Action.PROVISION, audit_action=audit_action, operation=operation,
        check_decommissioned=False, acl_action=Action.PROVISION,
    )
    # Бронь: создание OS-пользователя на хосте — деструктив. На занятом
    # чужим сервере разрешаем только владельцу брони или админу.
    reservation.ensure_not_reserved_for(identity, server, action=audit_action)
    reservation.ensure_not_acs_locked(identity, server)

    idempotency_key = read_idempotency_key(request)
    # Pre-check Idempotency-Key до любой generation creds. Если клиент
    # повторяет тот же POST с уже зарегистрированным ключом — оригинальная
    # task на worker'е применит сохранённые в её stash creds; никаких новых
    # password/ssh-keypair'ов мы не имеем права генерить и коммитить в БД,
    # иначе бокс получит OLD creds (из stash оригинала), а server-service-БД
    # перепишется NEW — drift на drift, SSH под БД-кредами сломан.
    if idempotency_key is not None:
        existing = await worker_client.lookup_existing_task(idempotency_key)
        if existing is not None:
            existing_id, existing_kind, existing_target = existing
            if existing_kind != task_kind or existing_target != server.id:
                audit_service.emit(
                    audit_action, target_id=account_id,
                    target_type="server_account",
                    status="failure", allowed=True,
                    details={
                        "reason": "idempotent_conflict",
                        "task_kind": task_kind,
                        "server_id": server.id,
                        "operation": operation,
                        "department_id": server.department_id,
                    },
                )
                raise ConflictError(
                    error_code="IDEMPOTENCY_KEY_REUSE_CONFLICT",
                    message=(
                        "Idempotency-Key already used for a different "
                        "operation (task_kind/target_server_id mismatch)"
                    ),
                    details={
                        "existing_task_kind": existing_kind,
                        "existing_target_server_id": existing_target,
                        "requested_task_kind": task_kind,
                        "requested_target_server_id": server.id,
                    },
                )
            audit_service.emit(
                audit_action, target_id=account_id,
                target_type="server_account",
                status="success", allowed=True,
                details={
                    "task_id": existing_id,
                    "task_kind": task_kind,
                    "server_id": server.id,
                    "operation": operation,
                    "login": account.login,
                    "department_id": server.department_id,
                    "idempotent_hit": True,
                },
            )
            return {
                "operation": operation,
                "server_id": server.id,
                "task_id": existing_id,
                "status": "queued",
            }

    # Idempotency-replay не сработал (ключа нет, либо ключ есть, но task'и
    # под ним ещё нет). Теперь применяем гарды, которые должны блокировать
    # новый dispatch, но не должны мешать replay'ю существующего:
    # decommissioned-server и discovered+no-password.
    _decommissioned_account_dispatch_guard(
        server=server,
        account_id=account_id,
        audit_action=audit_action,
        operation=operation,
    )
    if (
        account.source == AccountSource.DISCOVERED.value
        and account.password_encrypted is None
        and not force_password
    ):
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={
                "reason": "account_has_no_password",
                "server_id": server.id,
                "operation": operation,
                "department_id": server.department_id,
            },
        )
        raise ConflictError(
            error_code="ACCOUNT_HAS_NO_PASSWORD",
            message=(
                "Discovered account has no stored password; rotate the "
                "password first or pass ?force_password=true to generate a "
                "new one and overwrite the on-host password via chpasswd"
            ),
        )

    payload = _build_account_task_payload(
        server=server, account=account, include_attrs=True,
    )
    # `force_password=true` для discovered'а: сбрасываем сохранённое до ensure,
    # чтобы получить свежий пароль и force_replace=True. Managed-аккаунты
    # этот reset не трогает — контракт узкий: только discovered.
    had_password_before = account.password_encrypted is not None
    pending_before = bool(account.credentials_pending_apply)
    force_overwrite = (
        force_password
        and account.source == AccountSource.DISCOVERED.value
    )
    # Изоляция «creds-generation + dispatch» в одном savepoint'е: либо
    # коммитим обе мутации после успешного dispatch'а, либо роллбэчим
    # при ошибке dispatch'а. Без этого свежий ciphertext в БД без доехавшей
    # до worker'а задачи рвал бы SSH (drift между server-БД и боксом).
    creds_sp = await db.begin_nested()
    if force_overwrite:
        await account_svc.reset_provision_credentials(db, account)
    _, creds, _ = await account_svc.ensure_provision_credentials(
        db, account,
    )
    stash_key = worker_client.dispatch_creds_key(dispatch_creds_id())
    try:
        await worker_client.store_dispatch_creds(
            stash_key,
            {
                "password_plaintext": creds["password"],
                "ssh_private_key_plaintext": creds["ssh_private_key"],
            },
        )
    except ServiceUnavailableError:
        await creds_sp.rollback()
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "creds_store_unavailable",
                "task_kind": task_kind,
                "server_id": server.id,
                "operation": operation,
                "department_id": server.department_id,
            },
        )
        raise
    except Exception as exc:
        # Любой runtime-фейл Redis (timeout / conn refused / etc) —
        # best-effort cleanup stash'а и savepoint'а, 503 наружу.
        await worker_client.delete_dispatch_creds(stash_key)
        await creds_sp.rollback()
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "creds_store_failed",
                "task_kind": task_kind,
                "server_id": server.id,
                "operation": operation,
                "department_id": server.department_id,
                "error_class": type(exc).__name__,
            },
        )
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_UNAVAILABLE",
            message="Failed to stash provision credentials before dispatch",
        ) from exc
    payload["creds_stash_key"] = stash_key
    payload["ssh_public_key"] = creds["ssh_public_key"]
    # force_replace говорит воркеру «chpasswd на боксе». Три триггера:
    # (а) discovered + явный force_password — reset выше сбросил старое;
    # (б) пароля в БД до ensure не было — ensure сгенерил, надо доставить;
    # (в) `credentials_pending_apply=True` ещё до текущего dispatch'а —
    #     значит предыдущая попытка прошла dispatch, но callback'а не
    #     получили (worker мог упасть после dispatch'а); БД считает свой
    #     ciphertext «не подтверждённым», retry форсит overwrite.
    payload["force_replace"] = (
        force_overwrite or not had_password_before or pending_before
    )
    server_id_v = server.id
    server_dept_v = server.department_id
    account_login_v = account.login
    dispatch_ok = False
    idempotent_hit = False
    try:
        try:
            task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
                db=db,
                task_kind=task_kind,
                target_server_id=server_id_v,
                target_resource_id=account_id,
                payload=payload,
                created_by=identity.user_id,
                request_id=getattr(request.state, "request_id", None),
                idempotency_key=idempotency_key,
            )
            dispatch_ok = True
        except ConflictError:
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": "idempotent_conflict",
                    "task_kind": task_kind,
                    "server_id": server_id_v,
                    "operation": operation,
                    "department_id": server_dept_v,
                },
            )
            raise
        except ServiceUnavailableError:
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": "worker_unreachable",
                    "task_kind": task_kind,
                    "server_id": server_id_v,
                    "operation": operation,
                    "department_id": server_dept_v,
                },
            )
            raise
    finally:
        if not dispatch_ok:
            # Stash осиротел — task в Redis-broker не доехал, воркер за
            # creds не пойдёт; чистим вручную, чтобы plaintext не висел до TTL.
            await worker_client.delete_dispatch_creds(stash_key)
            await creds_sp.rollback()
    # Idempotent-hit на race-пути (UNIQUE race в `_dispatch_task_inner` после
    # нашего pre-check'а): новой публикации в брокер не было, оригинальная
    # task видит свой ciphertext по своему stash_key. Нашу свежую generation
    # КАТЕГОРИЧЕСКИ нельзя коммитить — иначе БД хранит NEW creds, бокс
    # получает OLD. Rollback savepoint'а + cleanup нашего stash'а.
    if idempotent_hit:
        await worker_client.delete_dispatch_creds(stash_key)
        await creds_sp.rollback()
    else:
        await creds_sp.commit()
    await db.commit()
    await db.refresh(account)
    audit_service.emit(
        audit_action, target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": task_kind,
            "server_id": server_id_v,
            "operation": operation,
            "login": account_login_v,
            "department_id": server_dept_v,
            "force_replace": payload.get("force_replace", False),
            "idempotent_hit": idempotent_hit,
        },
    )
    return {"operation": operation, "server_id": server_id_v, "task_id": task_id, "status": "queued"}


async def fanout_update_on_host(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account,
) -> list[dict]:
    """Разослать `account.update_on_host` (usermod) на все серверы аккаунта.

    Зовётся после PATCH'а управляемых атрибутов (`has_sudo`/`unix_groups`/
    `shell`) — синхронизирует уже сохранённое в БД состояние на боксы.

    Best-effort и неблокирующее: правка аккаунта уже закоммичена, fan-out —
    побочный эффект. Серверы без подтверждённого присутствия аккаунта
    (`present_on_server=False`) пропускаем — usermod на боксе, где юзера нет,
    упал бы. Списанные серверы пропускаем. Недоступность worker'а на отдельном
    сервере не валит остальные диспатчи и не валит ответ PATCH'а — она уходит
    в audit и в `skipped`.

    Возвращает список поставленных задач `{server_id, task_id}`.
    """
    audit_action = "server_account.update_on_host"
    idempotency_key = read_idempotency_key(request)
    request_id = getattr(request.state, "request_id", None)

    target_links = [
        link for link in account.server_links if link.present_on_server
    ]
    # Cap на размер fan-out'а. Один PATCH управляемого атрибута не должен
    # шедулить произвольное число dispatch'ей; при превышении эмитим
    # `truncated` audit-event и режем хвост (хост, попавший в truncated-хвост,
    # выровняется на следующем sweep/audit-цикле, ничего не теряется
    # необратимо).
    fanout_cap = get_settings().fanout_update_on_host_max
    if len(target_links) > fanout_cap:
        truncated_count = len(target_links) - fanout_cap
        audit_service.emit(
            "fanout_update_on_host.truncated",
            target_id=account.id, target_type="server_account",
            status="warning", allowed=True,
            details={
                "total_links": len(target_links),
                "cap": fanout_cap,
                "truncated_count": truncated_count,
                "source": "edit_fanout",
                "department_id": account.department_id,
            },
        )
        target_links = target_links[:fanout_cap]
    # Batch-load: вместо N `load_visible_server` round-trip'ов один
    # `WHERE id IN (...)`. Cross-dept / отсутствующие — просто не попадают
    # в map, фильтрация остаётся та же, что в старом цикле.
    servers_by_id = await server_svc.load_visible_servers(
        db, identity, [link.server_id for link in target_links],
    )

    tasks: list[dict] = []
    for link in target_links:
        server = servers_by_id.get(link.server_id)
        if server is None:
            continue
        if server.status == ServerStatus.DECOMMISSIONED:
            audit_service.emit(
                audit_action, target_id=account.id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": "decommissioned",
                    "server_id": server.id,
                    "operation": "update",
                    "source": "edit_fanout",
                    "department_id": server.department_id,
                },
            )
            continue
        per_server_key = f"{idempotency_key}:{server.id}" if idempotency_key else None
        payload = _build_account_task_payload(
            server=server, account=account, include_attrs=True,
            # update_on_host через usermod не двигает home — параллель с
            # точечным dispatch'ем (`account_update_on_host_dispatch`).
            include_home_dir=False,
        )
        try:
            task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
                db=db,
                task_kind="account.update_on_host",
                target_server_id=server.id,
                target_resource_id=account.id,
                payload=payload,
                created_by=identity.user_id,
                request_id=request_id,
                idempotency_key=per_server_key,
            )
            await db.commit()
        except (ConflictError, ServiceUnavailableError) as exc:
            reason = (
                "idempotent_conflict"
                if isinstance(exc, ConflictError)
                else "worker_unreachable"
            )
            audit_service.emit(
                audit_action, target_id=account.id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": reason,
                    "task_kind": "account.update_on_host",
                    "server_id": server.id,
                    "operation": "update",
                    "source": "edit_fanout",
                    "department_id": server.department_id,
                },
            )
            continue
        audit_service.emit(
            audit_action, target_id=account.id, target_type="server_account",
            status="success", allowed=True,
            details={
                "task_id": task_id,
                "task_kind": "account.update_on_host",
                "server_id": server.id,
                "operation": "update",
                "login": account.login,
                "source": "edit_fanout",
                "department_id": server.department_id,
                "idempotent_hit": idempotent_hit,
            },
        )
        tasks.append({"server_id": server.id, "task_id": task_id})
    return tasks


async def fanout_apply_credentials(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account,
    action: str,
    apply_password: bool,
    audit_action: str = "server_account.apply_credentials",
) -> tuple[list[dict], list[dict]]:
    """Пробросить пароль/ssh-ключ учётки на все привязанные сервера.

    Единый apply-механизм фичи «проброс кред»: ставит `account.update_on_host`
    на серверы, где аккаунт присутствует (`present_on_server=True`). Зовётся
    авто — после set/rotate пароля и ssh-ключа — и вручную через `POST .../apply`.

    Секреты в payload НЕ кладутся: по контракту воркер сам резолвит пароль
    (internal `fetch_account_password` для managed, self-сессия для non-managed),
    а публичный ssh-ключ — не секрет. В payload едут:

    * `apply_password: true` — просьба перезалить пароль на боксе (chpasswd).
      Выставляется только когда у учётки реально есть сохранённый пароль и
      caller хочет его пробросить (rotate пароля / ручной apply). Для apply
      после смены только ssh-ключа флаг не ставится.
    * `ssh_public_key` — текущий публичный ключ учётки (если задан), воркер
      кладёт его в authorized_keys.
    * `force_replace: true` — материал только что сменился в БД, на боксе ещё
      старый; форсим overwrite.

    `action` — право, которым авторизовать per-server dispatch (совпадает с
    гейтом вызвавшего endpoint'а: `rotate_password` для пароля/apply, `update`
    для ssh-ключа). Best-effort: недоступность воркера / decommissioned /
    reserved на отдельном сервере не валит остальные.

    Возвращает `(tasks, skipped)` — формы `{server_id, task_id}` и
    `{server_id, reason}`.
    """
    # Пароль просим перезалить только если он реально сохранён — иначе воркеру
    # нечего fetch'ить (discovered без пароля), и chpasswd не нужен.
    effective_apply_password = apply_password and account.password_encrypted is not None
    extra_payload: dict = {"force_replace": True}
    if effective_apply_password:
        extra_payload["apply_password"] = True
    if account.ssh_public_key is not None:
        extra_payload["ssh_public_key"] = account.ssh_public_key

    target_links = [
        link for link in account.server_links if link.present_on_server
    ]
    tasks: list[dict] = []
    skipped: list[dict] = []
    for link in target_links:
        try:
            result = await _dispatch_account_on_host(
                db=db, identity=identity, request=request,
                account_id=account.id, server_id=link.server_id,
                action=action, acl_action=action,
                audit_action=audit_action,
                task_kind="account.update_on_host",
                operation="apply",
                extra_payload=extra_payload,
                include_home_dir=False,
            )
        except ConflictError as exc:
            reason = {
                "SERVER_DECOMMISSIONED": "decommissioned",
                "SERVER_RESERVED": "reserved",
            }.get(exc.error_code, "idempotent_conflict")
            skipped.append({"server_id": link.server_id, "reason": reason})
            continue
        except ServiceUnavailableError:
            skipped.append({"server_id": link.server_id, "reason": "worker_unreachable"})
            continue
        except (NotFoundError, AuthorizationError):
            skipped.append({"server_id": link.server_id, "reason": "not_found_or_cross_dept"})
            continue
        tasks.append({"server_id": result["server_id"], "task_id": result["task_id"]})
    return tasks, skipped


async def recreate_login_orchestrate(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account,
    new_login: str,
) -> dict:
    """Оркестрация смены живого логина: deprovision → rename(БД) → provision.

    На каждый привязанный сервер ставится `account.deprovision` под СТАРЫМ
    логином (снести OS-пользователя), затем логин переименовывается в БД
    (синхронно с денормализованными копиями на связках), затем на каждый
    привязанный сервер ставится `account.provision` под НОВЫМ логином (завести
    заново + доставить пароль/ключ).

    Dispatch'и best-effort на decommissioned/unreachable серверах (они уезжают
    в `skipped`); rename в БД — обязателен и идёт в своей транзакции. Аудит
    CRITICAL эмитит вызывающий endpoint.

    Возвращает `{old_login, new_login, deprovision, provision, skipped}`.
    """
    old_login = account.login
    audit_dep = "server_account.deprovision"
    audit_prov = "server_account.provision"
    linked_ids = account_repo.linked_server_ids(account)

    deprovision: list[dict] = []
    provision: list[dict] = []
    skipped: list[dict] = []

    # 1. deprovision под старым логином на каждом сервере.
    for sid in linked_ids:
        try:
            result = await _dispatch_account_on_host(
                db=db, identity=identity, request=request,
                account_id=account.id, server_id=sid,
                action=Action.DEPROVISION,
                acl_action=Action.DEPROVISION,
                audit_action=audit_dep,
                task_kind="account.deprovision",
                operation="deprovision",
                include_home_dir=False,
            )
        except ConflictError as exc:
            reason = (
                "decommissioned"
                if exc.error_code == "SERVER_DECOMMISSIONED"
                else "idempotent_conflict"
            )
            skipped.append({"server_id": sid, "reason": f"deprovision_{reason}"})
            continue
        except ServiceUnavailableError:
            skipped.append({"server_id": sid, "reason": "deprovision_worker_unreachable"})
            continue
        except (NotFoundError, AuthorizationError):
            skipped.append({"server_id": sid, "reason": "deprovision_not_found_or_cross_dept"})
            continue
        deprovision.append({
            "server_id": result["server_id"],
            "operation": "deprovision",
            "task_id": result["task_id"],
        })

    # 2. rename в БД (обязательная мутация). Конфликт → 409 ACCOUNT_DUPLICATE.
    # Перечитываем аккаунт свежим — deprovision-диспатчи коммитили транзакцию,
    # исходный объект мог проэкспайриться; берём актуальную строку (+links).
    fresh = await account_repo.get_by_id(db, account.id)
    if fresh is None:
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND", message="Server account not found"
        )
    account = fresh
    await account_svc.rename_login_in_db(db, account, new_login)
    await db.commit()
    await db.refresh(account)

    # 3. provision под новым логином на каждом сервере.
    for sid in linked_ids:
        try:
            result = await _dispatch_account_provision(
                db=db, identity=identity, request=request,
                account_id=account.id, server_id=sid,
                audit_action=audit_prov,
                task_kind="account.provision",
                force_password=False,
            )
        except ConflictError as exc:
            reason = (
                "decommissioned"
                if exc.error_code == "SERVER_DECOMMISSIONED"
                else "idempotent_conflict"
            )
            skipped.append({"server_id": sid, "reason": f"provision_{reason}"})
            continue
        except ServiceUnavailableError:
            skipped.append({"server_id": sid, "reason": "provision_worker_unreachable"})
            continue
        except (NotFoundError, AuthorizationError):
            skipped.append({"server_id": sid, "reason": "provision_not_found_or_cross_dept"})
            continue
        provision.append({
            "server_id": result["server_id"],
            "operation": "provision",
            "task_id": result["task_id"],
        })

    return {
        "old_login": old_login,
        "new_login": new_login,
        "deprovision": deprovision,
        "provision": provision,
        "skipped": skipped,
    }


# ── account ↔ ВМ: provision / update / deprovision в госте ──────────────────
#
# Тот же общий пул учёток (`server_account`), что и у серверов, только цель —
# гость ВМ. Worker заходит на hub-сервер ВМ под управляющей учёткой, оттуда по
# `sshpass` в гостя и делает useradd/usermod/userdel. Пароль воркер тянет сам
# через internal (`fetch_account_password_by_id`) — в payload только публичные
# атрибуты. RBAC — те же действия матрицы server_account (provision/update/
# deprovision), что и у серверного пути.


def _build_vm_account_task_payload(
    *,
    hub,
    vm,
    account,
    include_attrs: bool,
    remove_home: bool | None = None,
) -> dict:
    """Payload для account-task'ов в гостя ВМ (provision / update / deprovision).

    Ключи адресации — hub'а (SSH-таргет всегда IP hub'а, не hostname), плюс
    `vm_id`/`vm_name`/`guest_ip` для входа в гостя и identity учётки. Секрет
    (пароль) в payload не кладём — воркер резолвит его через internal по
    `account_id`; `ssh_public_key` не секрет и едет как есть.
    """
    payload: dict = {
        # target_server_id dispatch'а — hub; в payload дублируем ключи адресации,
        # которые читает `open_hub_session` воркера.
        "server_id": hub.id,
        "hub_server_id": hub.id,
        "target_department_id": vm.department_id,
        "host": str(hub.ip_address),
        "ssh_port": hub.ssh_port,
        "is_managed": hub.is_managed,
        "management_user": hub.management_user,
        "vm_id": vm.id,
        "vm_name": vm.name,
        "guest_ip": str(vm.ip_address) if vm.ip_address is not None else None,
        "login": account.login,
        "account_id": account.id,
    }
    if include_attrs:
        payload["has_sudo"] = account.has_sudo
        payload["unix_groups"] = list(account.unix_groups)
        payload["ssh_public_key"] = account.ssh_public_key
    if remove_home is not None:
        payload["remove_home"] = remove_home
    return payload


async def _resolve_account_and_vm(
    *,
    db: AsyncSession,
    identity,
    account_id: str,
    vm_id: str,
    action: str,
    audit_action: str,
    operation: str,
    acl_action: str | None = None,
):
    """Permission + visibility + привязка для пары account+ВМ. Возвращает (account, vm, hub).

    Зеркало `_resolve_account_and_server`: авторизация аддитивная (роль ИЛИ
    per-account грант на `acl_action`). ВМ обязана быть в отделе аккаунта и
    привязана к нему (иначе 404). Hub ВМ обязан существовать и не быть списанным
    (иначе 409 HUB_UNAVAILABLE). На любом провале — failure-audit.
    """
    acl_action = acl_action or action
    has_role = await permissions.has_action(
        db, identity, EntityType.SERVER_ACCOUNT, action,
    )
    account = await account_repo.get_by_id(db, account_id)
    visible = account is not None and account.department_id == identity.department_id

    if has_role:
        if not visible:
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept", "operation": operation},
            )
            raise NotFoundError(
                error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
            )
    else:
        if not (
            visible
            and await permissions.has_account_action(db, identity, account, acl_action)
        ):
            details = {
                "vm_id": vm_id,
                "operation": operation,
                "reason": "permission_denied",
            }
            if getattr(identity, "subject_type", None) is not None:
                details["subject_type"] = identity.subject_type
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="denied", allowed=False, details=details,
            )
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message=f"No access to action '{acl_action}' on this server account",
                details={"entity_type": "server_account", "action": acl_action},
            )

    vm = await vm_repo.get_by_id(db, vm_id)
    # ВМ чужого отдела / несуществующая / не привязанная к учётке — единый 404,
    # как «нет учётки на этой ВМ» (не раскрываем чужую ВМ).
    if (
        vm is None
        or vm.department_id != account.department_id
        or not await account_repo.is_vm_linked(db, account_id, vm_id)
    ):
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "vm_not_linked", "vm_id": vm_id, "operation": operation},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this VM",
        )

    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "hub_unavailable", "vm_id": vm_id, "operation": operation},
        )
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    return account, vm, hub


async def _dispatch_vm_account_task(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account,
    vm,
    hub,
    task_kind: str,
    audit_action: str,
    operation: str,
    include_attrs: bool,
    remove_home: bool | None = None,
) -> dict:
    """Поставить одну account-task'у в гостя ВМ. target_server_id — hub, resource — учётка."""
    payload = _build_vm_account_task_payload(
        hub=hub, vm=vm, account=account,
        include_attrs=include_attrs, remove_home=remove_home,
    )
    # Managed-ВМ: воркер заходит в гостя по управляющему ключу (базовой учётки
    # `u` нет) — расшифровываем сохранённый mgmt-материал и кладём в Redis-stash.
    stash_key = await vm_svc.stash_existing_vm_mgmt_creds(vm, audit_action)
    if stash_key:
        payload["creds_stash_key"] = stash_key
    idempotency_key = read_idempotency_key(request)
    vm_id_v = vm.id
    vm_dept_v = vm.department_id
    account_login_v = account.login
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=task_kind,
            target_server_id=hub.id,
            target_resource_id=account.id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
        await db.commit()
    except ConflictError:
        if stash_key:
            await worker_client.delete_dispatch_creds(stash_key)
        audit_service.emit(
            audit_action, target_id=account.id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "idempotent_conflict", "task_kind": task_kind,
                "vm_id": vm_id_v, "operation": operation, "department_id": vm_dept_v,
            },
        )
        raise
    except ServiceUnavailableError:
        if stash_key:
            await worker_client.delete_dispatch_creds(stash_key)
        audit_service.emit(
            audit_action, target_id=account.id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "worker_unreachable", "task_kind": task_kind,
                "vm_id": vm_id_v, "operation": operation, "department_id": vm_dept_v,
            },
        )
        raise
    # Idempotent-hit: оригинальная задача несёт свой stash — свежий не нужен.
    if stash_key and idempotent_hit:
        await worker_client.delete_dispatch_creds(stash_key)
    audit_service.emit(
        audit_action, target_id=account.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "task_id": task_id, "task_kind": task_kind, "vm_id": vm_id_v,
            "operation": operation, "login": account_login_v,
            "department_id": vm_dept_v, "idempotent_hit": idempotent_hit,
        },
    )
    return {"operation": operation, "vm_id": vm_id_v, "task_id": task_id, "status": "queued"}


async def fanout_vm_provision(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account,
    vm_ids: list[str],
) -> tuple[list[dict], list[dict]]:
    """Best-effort `vm.account_provision` на набор ВМ (после attach с provision).

    Каждую ВМ резолвим через `_resolve_account_and_vm` (permission/привязка/hub);
    недоступный hub / worker / cross-dept ВМ уходят в `skipped`, не валят
    остальные. Возвращает `(tasks, skipped)` формы `{vm_id, task_id}` /
    `{vm_id, reason}`.
    """
    tasks: list[dict] = []
    skipped: list[dict] = []
    for vid in vm_ids:
        try:
            account_obj, vm, hub = await _resolve_account_and_vm(
                db=db, identity=identity, account_id=account.id, vm_id=vid,
                action=Action.PROVISION, acl_action=Action.PROVISION,
                audit_action="server_account.vm_provision", operation="provision",
            )
            result = await _dispatch_vm_account_task(
                db=db, identity=identity, request=request,
                account=account_obj, vm=vm, hub=hub,
                task_kind="vm.account_provision",
                audit_action="server_account.vm_provision",
                operation="provision", include_attrs=True,
            )
        except ConflictError as exc:
            reason = "hub_unavailable" if exc.error_code == "HUB_UNAVAILABLE" else "idempotent_conflict"
            skipped.append({"vm_id": vid, "reason": reason})
            continue
        except ServiceUnavailableError:
            skipped.append({"vm_id": vid, "reason": "worker_unreachable"})
            continue
        except (NotFoundError, AuthorizationError):
            skipped.append({"vm_id": vid, "reason": "not_found_or_cross_dept"})
            continue
        tasks.append({"vm_id": result["vm_id"], "task_id": result["task_id"]})
    return tasks, skipped


async def fanout_vm_deprovision(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account,
    login: str,
    vm_ids: list[str],
) -> None:
    """Best-effort `vm.account_deprovision` после отвязки от ВМ (userdel в госте).

    Связки в БД уже сняты, поэтому payload собираем из ВМ+hub напрямую (не из
    M2M-линка). Недоступность hub/worker не откатывает отвязку — фиксируем
    audit-warning'ом. Зеркало `_dispatch_deprovision_after_unlink` серверного пути.
    """
    audit_action = "server_account.vm_deprovision"
    request_id = getattr(request.state, "request_id", None)
    for vid in vm_ids:
        vm = await vm_repo.get_by_id(db, vid)
        if vm is None or vm.department_id != account.department_id:
            continue
        hub = await server_repo.get_by_id(db, vm.hub_server_id)
        if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
            audit_service.emit(
                audit_action, target_id=account.id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": "hub_unavailable", "vm_id": vid,
                    "source": "unlink_fanout", "department_id": vm.department_id,
                },
            )
            continue
        payload = {
            "server_id": hub.id,
            "hub_server_id": hub.id,
            "target_department_id": vm.department_id,
            "host": str(hub.ip_address),
            "ssh_port": hub.ssh_port,
            "is_managed": hub.is_managed,
            "management_user": hub.management_user,
            "vm_id": vm.id,
            "vm_name": vm.name,
            "guest_ip": str(vm.ip_address) if vm.ip_address is not None else None,
            "login": login,
            "account_id": account.id,
            "remove_home": False,
        }
        stash_key = None
        try:
            # Managed-ВМ: userdel в госте идёт по управляющему ключу — стэшим
            # расшифрованный mgmt-материал (недоступный Redis трактуем как
            # worker_unreachable и продолжаем).
            stash_key = await vm_svc.stash_existing_vm_mgmt_creds(vm, audit_action)
            if stash_key:
                payload["creds_stash_key"] = stash_key
            task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
                db=db, task_kind="vm.account_deprovision",
                target_server_id=hub.id, target_resource_id=account.id,
                payload=payload, created_by=identity.user_id, request_id=request_id,
            )
            await db.commit()
        except (ConflictError, ServiceUnavailableError) as exc:
            if stash_key:
                await worker_client.delete_dispatch_creds(stash_key)
            await db.rollback()
            reason = (
                "idempotent_conflict" if isinstance(exc, ConflictError)
                else "worker_unreachable"
            )
            audit_service.emit(
                audit_action, target_id=account.id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": reason, "vm_id": vid,
                    "source": "unlink_fanout", "department_id": vm.department_id,
                },
            )
            continue
        if stash_key and idempotent_hit:
            await worker_client.delete_dispatch_creds(stash_key)
        audit_service.emit(
            audit_action, target_id=account.id, target_type="server_account",
            status="success", allowed=True,
            details={
                "task_id": task_id, "vm_id": vid, "source": "unlink_fanout",
                "login": login, "department_id": vm.department_id,
            },
        )


async def fanout_vm_reprovision(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    account,
) -> tuple[list[dict], list[dict]]:
    """Best-effort re-provision учётки в гостях всех привязанных ВМ (после rotate).

    Ротация меняет общий пароль в БД; чтобы он доехал до гостей ВМ, на каждую
    привязанную ВМ ставим `vm.account_provision` (useradd идемпотентен, пароль
    воркер fetch'ит из БД и делает chpasswd). Best-effort — недоступность
    hub/worker на одной ВМ не валит остальные. Возвращает `(tasks, skipped)`.
    """
    vm_ids = account_repo.linked_vm_ids(account)
    if not vm_ids:
        return [], []
    return await fanout_vm_provision(
        db=db, identity=identity, request=request, account=account, vm_ids=vm_ids,
    )


# ── /servers/{id}/power/status — live BMC-probe через worker ────────────────


@router_servers.post(
    "/power/status",
    response_model=ServerPowerStatusDispatchResponse,
    status_code=202,
    summary="Запросить live состояние питания через BMC (202, worker)",
    description=(
        "Публикует задачу `power.status` в taskiq-broker. Worker через "
        "Redfish (HTTPS) либо ipmitool (legacy) запрашивает PowerState BMC "
        "и кладёт результат в `task.result`. Симметрия с power.on/off/reboot: "
        "тот же набор валидаций (visibility / role / decommissioned / "
        "no_ipmi). Доступ: `(server, *, power_status)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `power_status` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404) либо NO_IPMI_CONTROLLER."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def power_status_dispatch(
    server_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerPowerStatusDispatchResponse:
    """Ставит `power.status` в очередь worker'а.

    Доступ: `(server, *, power_status)`.

    Возможные ошибки: 403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND,
    404 NO_IPMI_CONTROLLER, 409 SERVER_DECOMMISSIONED,
    409 TASK_IDEMPOTENT_CONFLICT, 503 WORKER_UNREACHABLE.

    Связано: `_dispatch_for_server`, `server_worker/src/tasks/power.py::power_status`.
    """
    result = await _dispatch_for_server(
        db=db, identity=identity, request=request,
        server_id=server_id,
        action=Action.POWER_STATUS,
        audit_action="server.power_status",
        task_kind="power.status",
        require_ipmi=True,
    )
    return ServerPowerStatusDispatchResponse(**result)


# ── /servers/{id}/inventory/sync — SSH-based fact collection ────────────────


@router_servers.post(
    "/inventory/sync",
    response_model=ServerTaskDispatchResponse,
    summary="Запустить inventory-sync через SSH (202, worker)",
    status_code=202,
    description=(
        "Публикует задачу `inventory.sync` в taskiq-broker. Worker идёт на "
        "сервер по SSH под управляющим пользователем (`management_user`) — "
        "сервер обязан быть подготовлен (`is_managed`, через prepare), иначе "
        "409 PREPARE_REQUIRED. Снимает OS/kernel/packages/disks и постит facts "
        "обратно через `submit_inventory_facts` (internal endpoint). Сырые "
        "facts остаются в `task.result` для диагностики; submit-fail уходит в "
        "audit как `server.inventory_sync` failure, но сам task остаётся "
        "SUCCEEDED.\n\n"
        "Право `inventory_trigger` шарится с hardware-инвентаризацией и "
        "OS-user инвентаризацией (`POST /servers/{id}/users/inventory`) — "
        "отдельного `users_inventory_trigger` нет."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `inventory_trigger` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / PREPARE_REQUIRED (сервер не prepared) / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def inventory_sync_dispatch(
    server_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Ставит `inventory.sync` в очередь worker'а.

    Доступ: `(server, *, inventory_trigger)`. Сервер обязан быть prepared.

    Возможные ошибки: 403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND,
    409 SERVER_DECOMMISSIONED, 409 PREPARE_REQUIRED, 409 TASK_IDEMPOTENT_CONFLICT,
    503 WORKER_UNREACHABLE.

    Связано: `_dispatch_for_server`, `server_worker/src/tasks/inventory.py`.
    """
    # SSH-сбор не нуждается в BMC — `require_ipmi=False`. Требует prepare.
    result = await _dispatch_for_server(
        db=db, identity=identity, request=request,
        server_id=server_id,
        action=Action.INVENTORY_TRIGGER,
        audit_action="server.inventory_sync",
        task_kind="inventory.sync",
        require_ipmi=False,
        require_prepared=True,
    )
    return ServerTaskDispatchResponse(**result)


# ── /servers/{id}/astra-update — обновление ОС до версии каталога ────────────


@router_servers.post(
    "/astra-update",
    response_model=ServerTaskDispatchResponse,
    status_code=202,
    summary="Обновить ОС Astra до версии каталога через worker (202)",
    description=(
        "Публикует задачу `server.astra_update` в taskiq-broker. Воркер заходит "
        "на сервер по SSH под управляющим пользователем (сервер обязан быть "
        "`is_managed`), ПОЛНОСТЬЮ перезаписывает `/etc/apt/sources.list` "
        "репозиториями выбранной версии ОС (`OsVersion.repositories`) и гонит "
        "`apt update && astra-update -A -T -r`.\n\n"
        "На время обновления сервер помечается `busy_state='updating'` — "
        "системная блокировка, отбивающая ЛЮБЫЕ другие операции над сервером "
        "(prepare / inventory / power / account-ops / console) с 409 "
        "`SERVER_UPDATING`, включая владельца брони и админа. Блокировку "
        "снимает callback воркера (успех/ошибка); при успехе сервер "
        "привязывается к целевой версии и запускается inventory.sync.\n\n"
        "Задача одноразовая (max_attempts=1): упавшее обновление не "
        "повторяется автоматически.\n\n"
        "Доступ: `(server, *, update)` — как у prepare/rotate. Аудит — WARNING."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `update` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404) либо OS_VERSION_NOT_FOUND."},
        409: {"description": "SERVER_DECOMMISSIONED / SERVER_UPDATING (уже идёт обновление) / SERVER_RESERVED / PREPARE_REQUIRED / OS_VERSION_NO_REPOSITORIES / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def server_astra_update_dispatch(
    server_id: str,
    body: ServerAstraUpdateRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Ставит `server.astra_update` (обновление ОС) в очередь worker'а.

    Доступ: `(server, *, update)`. Порядок проверок — как у prepare:
    permission → visibility → decommissioned → not-reserved/not-updating →
    prepared → каталог-версия с непустыми репозиториями. Ставит
    `busy_state='updating'` и диспатчит задачу; на фейле dispatch'а
    блокировка откатывается.

    Связано: `server_worker/src/tasks/astra_update.py::server_astra_update`,
    callback `record_server_astra_updated`.
    """
    audit_action = "server.astra_update"
    task_kind = "server.astra_update"

    # 1. Role-check (resource-level, как prepare). Идёт первой по канону
    # permission → visibility: не делает existence-oracle на 403.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.UPDATE
        )

    # 2. Visibility + dept isolation.
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise

    # 3. Decommissioned-gate.
    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept worker operations",
        )

    # 3.4. Row-lock перед гейтами состояния и постановкой updating. Без него два
    # почти одновременных astra-update прошли бы «не updating»-проверку оба и оба
    # задиспатчили бы задачу (двойной apt/astra-update на боксе). FOR UPDATE
    # сериализует переход: второй запрос ждёт коммит первого и видит уже
    # выставленный updating → отбивается 409 SERVER_UPDATING ниже.
    db.expire(server)
    locked = await server_repo.get_for_update(db, server_id)
    if locked is None:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "vanished"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    server = locked

    # 3.5. Уже идёт обновление → 409 SERVER_UPDATING; чужая бронь → 409
    # SERVER_RESERVED. Оба через общий гейт (updating-check внутри блокирует
    # всех, reserved-check пропускает владельца/админа).
    reservation.ensure_not_reserved_for(identity, server, action=audit_action)
    reservation.ensure_not_acs_locked(identity, server)

    # 4. Prepared-gate: обновление идёт по SSH под управляющим ключом, до
    # prepare заходить нечем.
    require_server_prepared(server, audit_action=audit_action)

    # 5. Каталог-версия обязана существовать и иметь непустой список
    # репозиториев — иначе sources.list переписать нечем.
    os_version = await os_version_repo.get_by_id(db, body.os_version_id)
    if os_version is None:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "os_version_not_found", "os_version_id": body.os_version_id},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="Target OS version not found in catalog",
        )
    repositories = list(os_version.repositories or [])
    if not repositories:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "os_version_no_repositories",
                "os_version_id": body.os_version_id,
            },
        )
        raise ConflictError(
            error_code="OS_VERSION_NO_REPOSITORIES",
            message=(
                "Target OS version has an empty repositories list; nothing to "
                "write into sources.list"
            ),
        )

    # 6. Ставим системную блокировку updating и диспатчим задачу. Блокировку
    # ставим ДО dispatch'а (никто не должен вклиниться между), но на фейле
    # dispatch'а откатываем — иначе сервер завис бы в updating без задачи.
    now = datetime.now(timezone.utc)
    await server_repo.update(db, server, {
        "busy_state": BusyState.UPDATING,
        "busy_user_id": identity.user_id,
        "busy_actor_type": BusyActorType.USER,
        "busy_service_name": None,
        "busy_since": now,
        "busy_note": f"Обновление ОС Astra до {os_version.name}",
    })
    await db.commit()

    extra_payload = {
        "os_version_id": os_version.id,
        "repositories": repositories,
    }
    idempotency_key = read_idempotency_key(request)
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=task_kind,
            target_server_id=server.id,
            payload=build_ssh_task_payload(
                server=server,
                resolved_account_id=None,
                extra_payload=extra_payload,
            ),
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
            max_attempts=1,
        )
        await db.commit()
    except (ConflictError, ServiceUnavailableError) as exc:
        # Dispatch не прошёл — снимаем только что поставленную блокировку,
        # иначе сервер завис бы в updating без задачи, которая его освободит.
        await db.rollback()
        server = await server_repo.get_by_id(db, server_id)
        if server is not None and server.busy_state == BusyState.UPDATING:
            await server_repo.update(db, server, {
                "busy_state": BusyState.FREE,
                "busy_user_id": None,
                "busy_actor_type": BusyActorType.USER,
                "busy_service_name": None,
                "busy_since": None,
                "busy_note": None,
            })
            await db.commit()
        reason = (
            "idempotent_conflict"
            if isinstance(exc, ConflictError)
            else "worker_unreachable"
        )
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": reason,
                "task_kind": task_kind,
                "os_version_id": os_version.id,
                "department_id": server.department_id if server else None,
            },
        )
        raise
    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": task_kind,
            "os_version_id": os_version.id,
            "os_version_name": os_version.name,
            "repositories_count": len(repositories),
            "department_id": server.department_id,
            "idempotent_hit": idempotent_hit,
        },
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")


# ── /servers/{id}/acs-snapshots — снимки диска через ACS ────────────────────


async def _ensure_acs_available(
    db: AsyncSession, *, server, audit_action: str,
) -> None:
    """Общие ворота ACS для create/restore, поверх обычной action-матрицы.

    Право на действие уже проверено выше — здесь два дополнительных гейта:
    платформенный кил-свитч (`AcsSettings.enabled`) и per-department opt-in
    (`AcsDepartmentAccess.is_enabled`). Оба должны быть true. Выключенный
    платформенно ACS — временная недоступность (админ может включить в любой
    момент) → 503; отдел без opt-in — управленческое решение, не временное
    состояние → 403.
    """
    settings = await acs_settings_svc.get_acs_settings(db)
    if not settings.enabled:
        audit_service.emit(
            audit_action, target_id=server.id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "acs_disabled", "department_id": server.department_id},
        )
        raise ServiceUnavailableError(
            error_code="ACS_DISABLED",
            message="ACS snapshots are disabled or not configured on this platform",
        )
    dept_enabled = await acs_settings_svc.is_department_acs_enabled(
        db, server.department_id
    )
    if not dept_enabled:
        audit_service.emit(
            audit_action, target_id=server.id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "acs_department_not_enabled", "department_id": server.department_id},
        )
        raise AuthorizationError(
            error_code="ACS_DEPARTMENT_NOT_ENABLED",
            message="ACS snapshots are not enabled for this server's department",
        )


def _acs_task_payload(server, os_version) -> dict:
    """Payload для `acs.snapshot_create`/`acs.snapshot_restore`.

    `hostname` — это буквально ACS server-class (`LowServer`, `MiddleServer2`,
    ...), не техническое DNS-имя: воркер передаёт его как `stand_name` в
    `save-disk`/`restore-backup`. `version_name` (= `os_version.name`) воркер
    склеивает со `stand_name` в имя снимка (`{hostname}-{version_name}`).
    `host`/`ssh_port` — отдельно, для reachability-проб воркера по IP
    (короткие имена не резолвятся из пода).
    """
    return {
        "server_id": server.id,
        "os_version_id": os_version.id,
        "version_name": os_version.name,
        "host": str(server.ip_address),
        "hostname": server.hostname,
        "ssh_port": server.ssh_port,
        "target_department_id": server.department_id,
    }


async def _acs_resolve_and_dispatch(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    server,
    audit_action: str,
    task_kind: str,
    os_version_id: str,
    require_prepared: bool,
    require_bootstrap_password: bool,
    busy_note_action: str,
) -> str:
    """Общий хвост create/restore ACS-dispatch'а для уже видимого сервера.

    Caller отвечает за permission-check + visibility (single — через
    `get_server` с exception-хэндлингом, batch — через `load_visible_servers`).
    Здесь — общая цепочка гейтов, идентичная для обоих направлений, кроме
    двух точек ветвления по флагам:

      * `require_prepared` — только create (сервер обязан быть managed);
      * `require_bootstrap_password` — только restore (нужен для авто-prepare
        после reimage).

    Порядок: decommissioned → vms-hub-gate → row-lock → reservation/acs-busy →
    prepared-gate (create) → ACS-доступность (platform+department) →
    каталог-версия → bootstrap-пароль (restore) → busy_state=acs → dispatch →
    audit. Возвращает `task_id`; любой гейт поднимает исключение — caller
    (single или batch) сам решает, как это превратить в HTTP-ответ.
    """
    server_id = server.id

    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept worker operations",
        )

    if server.is_vms_hub:
        # VMS-hub несёт чужие ВМ и специфичный LVM/сетевой setup — полная
        # перезапись диска через ACS снесла бы всё это заодно с ОС хаба.
        # Отдельный гейт поверх decommissioned/reservation: hub может быть
        # вполне живым и не занятым, риск чисто в его роли.
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_is_vms_hub", "department_id": server.department_id},
        )
        raise ConflictError(
            error_code="SERVER_IS_VMS_HUB",
            message="VMS-hub servers cannot be targeted by ACS snapshot operations",
        )

    # Row-lock перед гейтами состояния — сериализует переход в acs, чтобы два
    # почти одновременных create/restore на один сервер не проскочили оба.
    db.expire(server)
    locked = await server_repo.get_for_update(db, server_id)
    if locked is None:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "vanished"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    server = locked

    reservation.ensure_not_reserved_for(identity, server, action=audit_action)
    if server.busy_state == BusyState.ACS:
        # Отдельный гейт поверх reservation: `ensure_not_reserved_for` не
        # знает про системный acs-лок. Без этого второй create/restore на уже
        # занятый ACS-операцией сервер прошёл бы для владельца/админа.
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "acs_in_progress", "department_id": server.department_id},
        )
        raise ConflictError(
            error_code="SERVER_ACS_BUSY",
            message="Server already has an ACS snapshot/restore operation in progress",
        )

    if require_prepared:
        require_server_prepared(server, audit_action=audit_action)

    await _ensure_acs_available(db, server=server, audit_action=audit_action)

    os_version = await os_version_repo.get_by_id(db, os_version_id)
    if os_version is None:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "os_version_not_found", "os_version_id": os_version_id},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message="Target OS version not found in catalog",
        )

    if require_bootstrap_password:
        bootstrap_status = await bootstrap_password_svc.get_bootstrap_password_status(
            db, os_version.id,
        )
        if bootstrap_status is None or not bootstrap_status["has_password"]:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "bootstrap_password_missing", "os_version_id": os_version.id},
            )
            raise ConflictError(
                error_code="ACS_BOOTSTRAP_PASSWORD_MISSING",
                message=(
                    "No bootstrap password configured for this OS version catalog "
                    "entry; restore is unavailable until the owner sets one via "
                    "PUT /os-versions/{id}/bootstrap-password"
                ),
            )

    now = datetime.now(timezone.utc)
    pre_acs_snapshot = reservation.capture_pre_acs_state(server)
    # Держатель ACS-брони — сам сервис, не человек, нажавший кнопку: снимок
    # идёт часами и переживает сессию инициатора, а прежняя пользовательская
    # бронь лежит в pre_acs_busy_snapshot и вернётся по завершении. Инициатор
    # остаётся в аудите (`server.acs_snapshot_create` / `..._restore`).
    await server_repo.update(db, server, {
        "busy_state": BusyState.ACS,
        "busy_user_id": None,
        "busy_actor_type": BusyActorType.SERVICE,
        "busy_service_name": SERVICE_RESERVATION_ACS,
        "busy_since": now,
        "busy_note": f"{busy_note_action}|{os_version.name}",
        "pre_acs_busy_snapshot": pre_acs_snapshot,
    })
    await db.commit()

    payload = _acs_task_payload(server, os_version)
    idempotency_key = read_idempotency_key(request)
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=task_kind,
            target_server_id=server.id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
            max_attempts=1,
        )
        await db.commit()
    except (ConflictError, ServiceUnavailableError) as exc:
        await db.rollback()
        server = await server_repo.get_by_id(db, server_id)
        if server is not None and server.busy_state == BusyState.ACS:
            reservation.restore_pre_acs_state(server)
            await db.flush()
            await db.commit()
        reason = (
            "idempotent_conflict"
            if isinstance(exc, ConflictError)
            else "worker_unreachable"
        )
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": reason,
                "task_kind": task_kind,
                "os_version_id": os_version.id,
                "department_id": server.department_id if server else None,
            },
        )
        raise
    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": task_kind,
            "os_version_id": os_version.id,
            "os_version_name": os_version.name,
            "department_id": server.department_id,
            "idempotent_hit": idempotent_hit,
        },
    )
    return task_id


async def _acs_visible_server_or_audit(
    db: AsyncSession, identity, server_id: str, *, audit_action: str,
):
    """`get_server` + audit на visibility-отказе — общий хвост single-dispatch'ей.

    NotFoundError — visibility-404 (cross-dept / нет row): caller прошёл
    permission, цель невидима → `failure`/`allowed=True`. AuthorizationError —
    отказ VIEW (роль с конкретным action, но без VIEW) → `denied`/`allowed=False`.
    """
    try:
        return await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise


@router_servers.post(
    "/acs-snapshots",
    response_model=ServerTaskDispatchResponse,
    status_code=202,
    summary="Создать снимок диска сервера через ACS (202, dispatch acs.snapshot_create)",
    description=(
        "Ставит задачу `acs.snapshot_create` в server_worker: ACS (Clonezilla-"
        "обёртка) снимает полный образ диска сервера (`stand_name=hostname`). "
        "Сервер обязан быть prepared. ACS должен быть включён платформенно "
        "(`AcsSettings.enabled`) и для отдела сервера (`AcsDepartmentAccess`). "
        "Ставит `busy_state=acs` до callback'а `record_acs_snapshot_created` "
        "(снимается в любом исходе — create не переписывает диск); на фейле "
        "dispatch'а блокировка откатывается сразу.\n\n"
        "Доступ: `(server, *, acs_snapshot_create)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет `acs_snapshot_create` либо ACS_DEPARTMENT_NOT_ENABLED."},
        404: {"description": "Сервер не найден / чужой dept, либо OS_VERSION_NOT_FOUND."},
        409: {"description": "SERVER_DECOMMISSIONED / SERVER_IS_VMS_HUB / SERVER_RESERVED / SERVER_UPDATING / SERVER_ACS_BUSY / PREPARE_REQUIRED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "ACS_DISABLED либо worker недоступен."},
    },
)
async def server_acs_snapshot_create_dispatch(
    server_id: str,
    body: ServerAcsSnapshotCreateRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Ставит `acs.snapshot_create` в очередь worker'а.

    Порядок проверок — как у `server_astra_update_dispatch`: permission →
    visibility → общий хвост `_acs_resolve_and_dispatch` (decommissioned →
    vms-hub → row-lock → reservation/acs-busy → prepared-gate → ACS-доступность
    → каталог-версия → dispatch).

    Связано: `server_worker/src/tasks/acs_snapshots.py::acs_snapshot_create`,
    callback `record_acs_snapshot_created`.
    """
    audit_action = "server.acs_snapshot_create"
    task_kind = "acs.snapshot_create"

    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.ACS_SNAPSHOT
        )

    server = await _acs_visible_server_or_audit(
        db, identity, server_id, audit_action=audit_action,
    )

    task_id = await _acs_resolve_and_dispatch(
        db=db, identity=identity, request=request, server=server,
        audit_action=audit_action, task_kind=task_kind,
        os_version_id=body.os_version_id,
        require_prepared=True,
        require_bootstrap_password=False,
        busy_note_action="save",
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")


@router_servers.post(
    "/acs-snapshots/restore",
    response_model=ServerTaskDispatchResponse,
    status_code=202,
    summary="Восстановить сервер из снимка ACS (202, dispatch acs.snapshot_restore)",
    description=(
        "Ставит задачу `acs.snapshot_restore` в server_worker: ACS полностью "
        "переписывает диск сервера снимком версии `os_version_id` "
        "(`stand_name=hostname`). Необратимо. Право `acs_snapshot_restore` — "
        "только тип-wide `admin`, инстанс-грант невозможен. Требует заранее "
        "заведённый bootstrap-пароль версии (`PUT /os-versions/{id}/bootstrap-"
        "password`) — без него после reimage авто-`server.prepare` зайти "
        "будет нечем. Ставит `busy_state=acs`; в отличие от create, при "
        "успехе НЕ снимается сразу — держится до завершения авто-prepare "
        "(см. callback `record_acs_snapshot_restore_done`)."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет тип-wide `acs_snapshot_restore` либо ACS_DEPARTMENT_NOT_ENABLED."},
        404: {"description": "Сервер не найден / чужой dept, либо OS_VERSION_NOT_FOUND."},
        409: {"description": "SERVER_DECOMMISSIONED / SERVER_IS_VMS_HUB / SERVER_RESERVED / SERVER_UPDATING / SERVER_ACS_BUSY / ACS_BOOTSTRAP_PASSWORD_MISSING / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "ACS_DISABLED либо worker недоступен."},
    },
)
async def server_acs_snapshot_restore_dispatch(
    server_id: str,
    body: ServerAcsSnapshotRestoreRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Ставит `acs.snapshot_restore` в очередь worker'а.

    `Action.ACS_SNAPSHOT` — один action на list/create/restore, из
    `_NON_INSTANCE_ACTIONS` (restore внутри — полная перезапись диска,
    слишком рискованно для точечных инстанс-грантов): проверяется
    `require_action` (тип-wide), не `require_resource_action` — по аналогии
    с `Action.VMS_HUB_PREPARE` (`services/vm.py`), тоже `_NON_INSTANCE_ACTIONS`,
    тоже таргетит конкретный сервер.

    Prepared-gate НЕ применяется (в отличие от create): restore — это как раз
    путь восстановления сервера, который может быть уже сломан/не managed;
    требовать is_managed до restore лишило бы смысла сам recovery-сценарий.

    Bootstrap-пароль на этом шаге только проверяется на существование
    (`get_bootstrap_password_status`) — реальный резолв и расшифровка идут в
    `record_acs_snapshot_restore_done` непосредственно перед dispatch'ем
    `server.prepare`, не здесь: так авто-prepare не зависит от TTL
    Redis-стэша, пережившего всё окно restore+reachability (10-30 минут).

    Связано: `server_worker/src/tasks/acs_snapshots.py::acs_snapshot_restore`,
    callback `record_acs_snapshot_restore_done`.
    """
    audit_action = "server.acs_snapshot_restore"
    task_kind = "acs.snapshot_restore"

    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.ACS_SNAPSHOT
        )

    server = await _acs_visible_server_or_audit(
        db, identity, server_id, audit_action=audit_action,
    )

    task_id = await _acs_resolve_and_dispatch(
        db=db, identity=identity, request=request, server=server,
        audit_action=audit_action, task_kind=task_kind,
        os_version_id=body.os_version_id,
        require_prepared=False,
        require_bootstrap_password=True,
        busy_note_action="restore",
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")


@router_servers.get(
    "/acs-snapshots",
    response_model=AcsSnapshotListResponse,
    summary="Список снимков ACS этого сервера (живой directory listing)",
    description=(
        "Читает `GET check-snapshots` у ACS и фильтрует по префиксу "
        "`{hostname}-` этого сервера — своей таблицы снимков нет, ACS сам "
        "хранит директорию. `version_name` — хвост имени после этого "
        "префикса. Доступ: `(server, *, acs_snapshot_list)`."
    ),
    responses={
        200: {"description": "Снимки этого сервера, отсортированы по имени."},
        403: {"description": "Нет `acs_snapshot_list` либо ACS_DEPARTMENT_NOT_ENABLED."},
        404: {"description": "Сервер не найден / чужой dept."},
        503: {"description": "ACS_DISABLED либо ACS недоступен (timeout/unreachable)."},
    },
)
async def server_acs_snapshot_list(
    server_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> AcsSnapshotListResponse:
    """Живой список снимков ACS, отфильтрованный по hostname этого сервера.

    В отличие от create/restore, ничего не диспатчит в worker — ACS читается
    синхронно прямо здесь (`ACSClient.list_snapshots`), пароль расшифровывает
    `acs_settings_svc.get_acs_credentials`. Ошибки ACS (timeout/unreachable/
    ошибочный HTTP-статус) пробрасываются как `ServiceUnavailableError` без
    перехвата — тот же `ACS_TIMEOUT`/`ACS_UNREACHABLE`/`ACS_ERROR`, что кидает
    сам клиент.
    """
    audit_action = "server.acs_snapshot_list"

    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.ACS_SNAPSHOT
        )

    server = await _acs_visible_server_or_audit(
        db, identity, server_id, audit_action=audit_action,
    )

    await _ensure_acs_available(db, server=server, audit_action=audit_action)

    try:
        acs_url, acs_password = await acs_settings_svc.get_acs_credentials(db)
        all_names = await acs_client.list_snapshots(acs_url, acs_password)
    except ServiceUnavailableError as exc:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": exc.error_code.lower(), "department_id": server.department_id},
        )
        raise

    prefix = f"{server.hostname}-"
    items = sorted(
        (
            AcsSnapshotItem(name=name, version_name=name[len(prefix):])
            for name in all_names
            if name.startswith(prefix)
        ),
        key=lambda item: item.name,
    )

    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "department_id": server.department_id,
            "snapshot_count": len(items),
        },
    )
    return AcsSnapshotListResponse(snapshots=items)


@router_servers.get(
    "/acs-availability",
    response_model=AcsAvailabilityResponse,
    summary="Можно ли показывать снимки ACS для этого сервера (всегда 200)",
    description=(
        "Лёгкая read-only проверка для фронта — показывать ли вкладку "
        "«Снимки ACS»: право `acs_snapshot` (list/create/restore — один action, "
        "любая роль с этим грантом, не хардкод `admin`) + платформенный "
        "`AcsSettings.enabled` + `AcsDepartmentAccess` отдела сервера. Держатель "
        "гранта получает все три возможности сразу — отдельных can_create/"
        "can_restore не нужно. Не проверяет реальную сетевую доступность ACS "
        "(timeout/unreachable) — это решается в момент настоящего "
        "`GET .../acs-snapshots`. Аудит не пишет — это не действие, а фоновая "
        "проверка видимости UI."
    ),
    responses={
        200: {"description": "{available: true|false}."},
        404: {"description": "Сервер не найден / чужой dept."},
    },
)
async def server_acs_availability(
    server_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> AcsAvailabilityResponse:
    """Возвращает `available=false` вместо 403/503 — это capability-check, не действие."""
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError):
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )

    if not await permissions.has_action(db, identity, EntityType.SERVER, Action.ACS_SNAPSHOT):
        return AcsAvailabilityResponse(available=False)

    settings = await acs_settings_svc.get_acs_settings(db)
    if not settings.enabled:
        return AcsAvailabilityResponse(available=False)

    dept_enabled = await acs_settings_svc.is_department_acs_enabled(
        db, server.department_id
    )
    return AcsAvailabilityResponse(available=dept_enabled)


# ── /servers/{id}/prepare — bootstrap управления (онбординг) ────────────────


@router_servers.post(
    "/prepare",
    response_model=ServerPrepareResponse,
    status_code=202,
    summary="Бутстрап управления сервером через worker (202)",
    description=(
        "Публикует задачу `server.prepare` в taskiq-broker. Два режима выбора "
        "bootstrap-кред (ровно один): `{account_id}` — server_service сам "
        "резолвит привязанный к серверу server_account и расшифровывает его "
        "пароль (+ ssh-ключ, если есть), UI пароль не шлёт; либо ручной "
        "`{username_b64, password_b64, ssh_private_key_b64?}` (логин/пароль в "
        "base64, симметрия с reveal-картами). server_service декодирует/резолвит "
        "креды и прокидывает воркеру через cross-DB dispatch-канал; воркер "
        "заходит на сервер под ними по SSH, заводит системного управляющего "
        "пользователя DBOS, даёт ему sudo и кладёт публичный ключ управления — "
        "дальше управление по ключу без исходного пароля.\n\n"
        "Bootstrap-креды одноразовые и НЕ хранятся персистентно: воркер стирает "
        "их из task-payload сразу после чтения. По завершении воркер POST'ит "
        "callback `/internal/.../prepared` — server_service помечает сервер "
        "`is_managed`.\n\n"
        "Идемпотентно: повторный prepare не падает, если управляющий "
        "пользователь и ключ уже есть.\n\n"
        "Доступ: `(server, *, update)` — бутстрап это management-операция над "
        "сервером; отдельного action не заводим. Аудит — CRITICAL."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `update` (либо `view_password` в account-режиме) либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404) либо ACCOUNT_NOT_FOUND / ACCOUNT_NOT_LINKED в account-режиме."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT / ACCOUNT_HAS_NO_PASSWORD (account-режим)."},
        422: {"description": "Битый base64 / нарушение режима (account_id вместе с ручными полями, либо ни того ни другого) / слабый ручной пароль."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP prepare-rate-limit пробит."},
        503: {"description": "Worker недоступен — WORKER_REDIS_UNAVAILABLE (Redis-stash для bootstrap-кред недоступен) или WORKER_UNREACHABLE / WORKER_REDIS_NOT_CONFIGURED (dispatch в taskiq)."},
    },
)
@endpoint_limiter.limit(get_settings().server_prepare_rate_limit)
async def server_prepare_dispatch(
    request: Request,
    server_id: str,
    body: ServerPrepareRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerPrepareResponse:
    """Ставит `server.prepare` (бутстрап управления) в очередь worker'а.

    Доступ: `(server, *, update)`. Битый base64 в теле → 422 (валидатор схемы
    срабатывает до этого хендлера). Связано:
    `server_worker/src/tasks/prepare.py::server_prepare`.
    """
    audit_action = "server.prepare"
    task_kind = "server.prepare"

    # Все checks ДО `store_prepare_creds` — иначе любой authenticated caller
    # без прав / cross-dept может забивать Redis TTL'd-плейнтекстом (и audit
    # denied-emit пройдёт уже после store). Permission → visibility →
    # decommissioned, тот же порядок, что у `_dispatch_for_server`.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.UPDATE
        )

    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        # NotFoundError — visibility-404 (cross-dept / нет row): failure+allowed=True.
        # AuthorizationError — нет VIEW: denied+allowed=False.
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise

    resolve_creds = _make_prepare_creds_resolver(
        db=db, identity=identity, prepare_req=body,
        server_id=server_id, audit_action=audit_action,
    )

    task_id = await _prepare_resolve_and_dispatch(
        db=db, identity=identity, request=request,
        server=server, audit_action=audit_action, task_kind=task_kind,
        resolve_creds=resolve_creds,
    )
    return ServerPrepareResponse(task_id=task_id, status="queued")


@router_servers.post(
    "/install-node-exporter",
    response_model=ServerTaskDispatchResponse,
    status_code=202,
    summary="Установить node_exporter на сервере (202, dispatch server.install_node_exporter)",
    description=(
        "Ставит задачу `server.install_node_exporter` в server_worker. "
        "Worker заходит на managed-сервер по DBOS управляющему ключу и выполняет "
        "idempotent-установку node_exporter под sudo. Нужен для Grafana-панелей "
        "`var-node=<ip>:9100`.\n\n"
        "Доступ: тот же, что у prepare-dispatch — `(server, update)`. Сервер "
        "обязан быть prepared (`is_managed=true`), иначе 409 PREPARE_REQUIRED."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `update`."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / PREPARE_REQUIRED / SERVER_UPDATING / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def install_node_exporter_dispatch(
    request: Request,
    server_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """POST /servers/{id}/install-node-exporter — dispatch managed SSH task."""
    audit_action = "server.node_exporter_installed"
    task_kind = "server.install_node_exporter"

    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.UPDATE
        )

    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise

    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept worker operations",
        )
    if not server.is_managed:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "prepare_required", "department_id": server.department_id},
        )
        raise ConflictError(
            error_code="PREPARE_REQUIRED",
            message="Server is not prepared; run prepare before installing node_exporter",
        )
    reservation.ensure_not_updating(identity, server, action=audit_action)
    reservation.ensure_not_acs_locked(identity, server)

    task_id, _ = await dispatch_server_ssh_task(
        db=db,
        identity=identity,
        request=request,
        server=server,
        task_kind=task_kind,
        audit_action=audit_action,
        resolved_account_id=None,
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")


def _make_prepare_creds_resolver(
    *,
    db: AsyncSession,
    identity,
    prepare_req,
    server_id: str,
    audit_action: str,
):
    """Собрать резолвер bootstrap-кред для prepare (account- или ручной режим).

    `prepare_req` — `ServerPrepareRequest` (single / clean) либо
    `ServerPrepareBatchItem` (batch): обе несут `is_account_mode()` / `username()`
    / `password()` / `ssh_private_key()`. Возвращает async-функцию
    `resolve_creds(server) -> dict`, которую `_prepare_resolve_and_dispatch`
    зовёт ПОСЛЕ idempotency-replay и decommissioned-gate.

    На отказе резолва (нет `view_password` / не привязан / нет пароля) сам
    эмитит failure-audit и поднимает исключение ДО `store_prepare_creds` — в
    Redis тогда ничего не ложится.
    """

    async def resolve_creds(srv) -> dict:
        # account_id — резолвим привязанный аккаунт, расшифровываем его пароль
        # (+ ssh-ключ, если есть); ручной — декодируем base64 (проверен схемой).
        if prepare_req.is_account_mode():
            try:
                with emit_denied_on_authz_error(
                    audit_action,
                    target_id=server_id,
                    target_type="server",
                    extra_details={
                        "server_id": server_id,
                        "denied_on": "account_view_password",
                        "account_id": prepare_req.account_id,
                    },
                    identity=identity,
                ):
                    resolved = await account_svc.resolve_bootstrap_credentials(
                        db, identity, prepare_req.account_id, srv,
                    )
            except (NotFoundError, ConflictError) as exc:
                audit_service.emit(
                    audit_action, target_id=server_id, target_type="server",
                    status="failure", allowed=True,
                    details={
                        "reason": exc.error_code.lower(),
                        "account_id": prepare_req.account_id,
                        "department_id": srv.department_id,
                    },
                )
                raise
            creds: dict = {
                "bootstrap_login": resolved["login"],
                "bootstrap_password": resolved["password"],
            }
            if resolved["ssh_private_key"] is not None:
                creds["bootstrap_ssh_private_key"] = resolved["ssh_private_key"]
            return creds
        creds = {
            "bootstrap_login": prepare_req.username(),
            "bootstrap_password": prepare_req.password(),
        }
        ssh_private_key = prepare_req.ssh_private_key()
        if ssh_private_key is not None:
            creds["bootstrap_ssh_private_key"] = ssh_private_key
        return creds

    return resolve_creds


async def _prepare_resolve_and_dispatch(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    server,
    audit_action: str,
    task_kind: str,
    resolve_creds,
) -> str:
    """Ядро prepare-dispatch'а для одного уже-загруженного сервера.

    Caller отвечает за permission + visibility (single — через
    `get_server`, bulk — через `load_visible_servers`). Здесь — общий хвост:
    idempotency-replay → decommissioned-gate → resolve creds → augment
    linked-accounts → Redis-stash → dispatch → audit, с тем же cleanup'ом
    осиротевшего stash'а на всех путях отказа.

    `resolve_creds(server) -> dict` собирает bootstrap-креды (manual / account-
    режим) и сам эмитит failure-audit при своём отказе. Зовётся ПОСЛЕ
    idempotency-replay и decommissioned-gate — на replay'е/списанном сервере
    мы не должны ни резолвить креды, ни класть их в Redis.

    Возвращает `task_id` поставленной задачи (на idempotency-replay-hit —
    existing id, с уже заэмиченным success-audit'ом). Любой decommissioned /
    idempotent_conflict / worker_unreachable пробрасывается исключением —
    caller (bulk) ловит его в skipped, single — пробрасывает наружу как
    HTTP-ошибку.
    """
    server_id = server.id

    idempotency_key = read_idempotency_key(request)

    # Idempotency-replay должен идти ДО `store_prepare_creds`. Иначе любой
    # повторный POST с тем же ключом плодит новые plaintext-stash'и в Redis
    # под orphan-ключами, которые задача никогда не прочтёт — каждый висит
    # PREPARE_CREDS_TTL_SECONDS (900s) до естественного истечения. Caller с
    # валидным `update` за это окно может забить Redis plaintext'ом.
    #
    # Также replay должен идти ДО decommissioned-check'а: повторный POST с
    # тем же ключом после decommission'а обязан вернуть existing task_id,
    # иначе retry-семантика клиента ломается на 409 SERVER_DECOMMISSIONED.
    if idempotency_key is not None:
        existing = await worker_client.lookup_existing_task(idempotency_key)
        if existing is not None:
            existing_id, existing_kind, existing_target = existing
            if existing_kind != task_kind or existing_target != server_id:
                audit_service.emit(
                    audit_action, target_id=server_id, target_type="server",
                    status="failure", allowed=True,
                    details={
                        "reason": "idempotency_key_reuse_conflict",
                        "task_kind": task_kind,
                        "department_id": server.department_id,
                    },
                )
                raise ConflictError(
                    error_code="IDEMPOTENCY_KEY_REUSE_CONFLICT",
                    message=(
                        "Idempotency-Key already used for a different "
                        "operation (task_kind/target_server_id mismatch)"
                    ),
                    details={
                        "existing_task_kind": existing_kind,
                        "existing_target_server_id": existing_target,
                        "requested_task_kind": task_kind,
                        "requested_target_server_id": server_id,
                    },
                )
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="success", allowed=True,
                details={
                    "task_id": existing_id,
                    "task_kind": task_kind,
                    "department_id": server.department_id,
                    "idempotent_hit": True,
                },
            )
            return existing_id

    # Idempotency-replay не сработал. Decommissioned-check теперь блокирует
    # новый dispatch — но не мешает retry'ю существующей задачи (см. ветку
    # выше, она уже return'нула existing task_id для replay'я).
    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept worker operations",
        )

    # Гейт обновления ОС: пока сервер `updating`, повторный prepare отбиваем —
    # bootstrap посреди astra-update раскатал бы управляющую учётку на
    # полуобновлённый бокс. Блокирует всех, включая владельца брони и админа.
    reservation.ensure_not_updating(identity, server, action=audit_action)
    reservation.ensure_not_acs_locked(identity, server)

    # Креды собирает caller-specific резолвер: single поддерживает account- и
    # ручной режимы, bulk — только ручной (у каждого бокса свои). Резолвер сам
    # эмитит failure-audit при отказе (нет права / не привязан / нет пароля) и
    # выходит ДО store_prepare_creds — в Redis тогда ничего не кладём.
    bootstrap_creds: dict = await resolve_creds(server)

    # Привязанные к серверу аккаунты worker должен завести сразу на этапе
    # prepare. Собираем их креды (password + ssh keypair + управляемые
    # атрибуты) здесь и кладём в тот же bootstrap-stash — в payload едет
    # только ссылка, plaintext в worker-БД не оседает. `ensure_provision_credentials`
    # генерит недостающие секреты (sticky по каждому) и помечает их
    # pending_apply — callback `submit_prepared` сам по себе их не подтверждает,
    # но это не хуже обычного provision-dispatch'а: следующий явный provision/
    # inventory приведёт состояние в актуальное. Discovered-аккаунт без пароля
    # заводим с ключом и без chpasswd.
    linked_accounts_payload: list[dict] = []
    linked_accounts = await account_repo.list_for_server(db, server_id)
    for acc in linked_accounts:
        # Discovered-аккаунт без пароля (password_encrypted IS NULL) едет в
        # stash без пароля: worker заведёт его useradd + ключ, без chpasswd.
        # `ensure_provision_credentials` тут выдумал бы и сохранил пароль —
        # аккаунт перестал бы быть безпарольным. Ключ нужен в любом случае,
        # поэтому поднимаем только SSH-пару.
        if acc.password_encrypted is None:
            _, ssh_creds = await account_svc.ensure_ssh_keypair(db, acc)
            password = None
            ssh_public_key = ssh_creds["ssh_public_key"]
            ssh_private_key = ssh_creds["ssh_private_key"]
        else:
            _, acc_creds, _ = await account_svc.ensure_provision_credentials(db, acc)
            password = acc_creds["password"]
            ssh_public_key = acc_creds["ssh_public_key"]
            ssh_private_key = acc_creds["ssh_private_key"]
        linked_accounts_payload.append({
            "account_id": acc.id,
            "login": acc.login,
            "password": password,
            "ssh_public_key": ssh_public_key,
            "ssh_private_key": ssh_private_key,
            "has_sudo": acc.has_sudo,
            "unix_groups": list(acc.unix_groups),
            "shell": acc.shell,
            "home_dir": acc.home_dir,
        })
    if linked_accounts_payload:
        bootstrap_creds["linked_accounts"] = linked_accounts_payload

    # Управляющий конфиг для воркера: имя учётки + пер-режимные группы/команды.
    # Резолвим ДО store_prepare_creds — имя управляющего пользователя нужно и
    # для mgmt_install-stash'а ниже, и для payload'а.
    mgmt_cfg = await management_user_config_svc.get_config(db)

    # Per-server управляющие креды (#3): генерим (или переиспользуем sticky)
    # пару+пароль пользователя dbos и кладём в тот же bootstrap-stash под
    # `mgmt_install`. Шифрованный материал сохраняется в БД в этой же
    # транзакции (commit вместе с dispatch'ем), воркер получает plaintext
    # транзиентно из Redis для bootstrap'а (authorized_keys + chpasswd).
    _, mgmt_creds, mgmt_generated = await management_creds_svc.ensure_management_credentials(
        db, server,
    )
    bootstrap_creds["mgmt_install"] = {
        "management_user": mgmt_cfg.login,
        "public_key": mgmt_creds["public_key"],
        "private_key": mgmt_creds["private_key"],
        "password": mgmt_creds["password"],
    }

    # Креды НЕ кладём в task-payload (иначе plaintext осел бы в worker-БД).
    # Пишем их в Redis под одноразовый ключ с TTL, в payload — только ссылка.
    # Воркер читает креды по ссылке на каждой попытке, TTL чистит их сам.
    creds_key = worker_client.prepare_creds_key(prepare_creds_id())
    try:
        await worker_client.store_prepare_creds(creds_key, bootstrap_creds)
    except ServiceUnavailableError:
        # Redis отдал свою же категорию ошибки (например WORKER_REDIS_NOT_CONFIGURED) —
        # пробрасываем как есть, только пишем audit-следом.
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "creds_store_unavailable",
                "task_kind": task_kind,
                "department_id": server.department_id,
            },
        )
        raise
    except Exception as exc:
        # Любой runtime-фейл Redis (timeout, connection refused, etc.) до этого
        # уходил наружу как 500 без audit-следа — атакующий мог ронять prepare
        # без отметки в журнале. Best-effort rollback + audit_failure + 503.
        await worker_client.delete_prepare_creds(creds_key)
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "creds_store_failed",
                "task_kind": task_kind,
                "department_id": server.department_id,
                "error_class": type(exc).__name__,
            },
        )
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_UNAVAILABLE",
            message="Failed to stash bootstrap credentials before dispatch",
        ) from exc

    # Управляющий конфиг (`mgmt_cfg`) уже резолвлен выше (до store). Источник
    # истины — ManagementUserConfig (singleton). Детект редакции происходит на
    # боксе, поэтому отдаём конфиг по всем четырём режимам, воркер выберет нужный
    # после детекта. login фоллбэчится на env-дефолт воркера, если в БД его нет.
    management_modes = {
        mode.value: cfg.model_dump(mode="json")
        for mode, cfg in mgmt_cfg.modes.items()
    }

    payload: dict = {
        "server_id": server_id,
        "target_department_id": server.department_id,
        # SSH — по IP, не по hostname (короткие имена не резолвятся из пода).
        "host": str(server.ip_address),
        "ssh_port": server.ssh_port,
        "is_managed": server.is_managed,
        "management_user": server.management_user,
        "management_login": mgmt_cfg.login,
        "management_modes": management_modes,
        "bootstrap_creds_key": creds_key,
    }
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=task_kind,
            target_server_id=server_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
        await db.commit()
    except ConflictError:
        # Подчищаем Redis: воркер за креды не пойдёт, иначе plaintext висит до TTL.
        await worker_client.delete_prepare_creds(creds_key)
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "idempotent_conflict",
                "task_kind": task_kind,
                "department_id": server.department_id,
            },
        )
        raise
    except ServiceUnavailableError:
        await worker_client.delete_prepare_creds(creds_key)
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "worker_unreachable",
                "task_kind": task_kind,
                "department_id": server.department_id,
            },
        )
        raise

    # Race-окно: между нашим pre-check'ом и dispatch_task'ом конкурент успел
    # вставить row с тем же idempotency_key. dispatch_task поймал
    # IntegrityError и вернул существующий id — наш только что положенный
    # stash осиротел, чистим его, иначе plaintext висит до TTL.
    if idempotent_hit:
        await worker_client.delete_prepare_creds(creds_key)

    # Per-server управляющие креды сгенерированы в этом вызове (#3) — отдельный
    # CRITICAL-аудит, чтобы факт первой генерации был виден в SIEM рядом с
    # dispatch'ем prepare. Sticky-reuse (повторный prepare) его не эмитит.
    if mgmt_generated:
        audit_service.emit(
            "server.management_creds_generated",
            target_id=server_id, target_type="server",
            status="success", allowed=True,
            details={
                "management_user": mgmt_cfg.login,
                "department_id": server.department_id,
                "regenerated": False,
            },
        )

    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": task_kind,
            "department_id": server.department_id,
            "idempotent_hit": idempotent_hit,
        },
    )
    return task_id


# ── /servers/{id}/management-credentials/rotate — ротация per-server кред ────


@router_servers.post(
    "/management-credentials/rotate",
    response_model=ServerManagementCredsRotateResponse,
    status_code=202,
    summary="Ротация per-server управляющих кред через worker (202)",
    description=(
        "Публикует задачу `server.rotate_management_creds`. server_service "
        "генерит новую Ed25519-пару + пароль управляющего пользователя, "
        "переносит текущий ciphertext в `previous_mgmt_*` (анти-локаут), пишет "
        "новый в `mgmt_*`, ставит `mgmt_creds_pending_apply=True` и кладёт "
        "новый материал в Redis-stash под `creds_stash_key`. Worker заходит "
        "ДЕЙСТВУЮЩИМ ключом (fetch отдаёт previous, пока pending), ставит новый "
        "pubkey + chpasswd, проверяет вход новым ключом, затем POST'ит "
        "`/internal/.../management-credentials/applied` — server_service снимает "
        "pending и зануляет previous.\n\n"
        "Доступ: `(server, *, update)` — тот же гейт, что у prepare. Сервер "
        "обязан быть prepared (`is_managed`). Аудит — CRITICAL."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `update` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404)."},
        409: {"description": "SERVER_DECOMMISSIONED / PREPARE_REQUIRED (сервер не prepared) / MGMT_ROTATION_PENDING (предыдущая ротация не подтверждена) / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен — WORKER_REDIS_UNAVAILABLE (Redis-stash) или WORKER_UNREACHABLE / WORKER_REDIS_NOT_CONFIGURED."},
    },
)
async def server_rotate_management_credentials_dispatch(
    request: Request,
    server_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerManagementCredsRotateResponse:
    """Ставит `server.rotate_management_creds` в очередь worker'а (#3).

    Доступ: `(server, *, update)`. Связано:
    `services/management_creds.py::rotate_management_credentials`,
    `server_worker` (worker-task — отдельная волна).
    """
    audit_action = "server.management_creds_rotated"
    task_kind = "server.rotate_management_creds"

    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.UPDATE
        )

    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise

    # Row-lock перед проверкой pending_apply и переносом mgmt_*→previous_*. Без
    # него две почти одновременных ротации прочитали бы pending_apply=False обе,
    # обе перенесли бы текущий (ещё стоящий на боксе) ключ в previous, затерев
    # его новым нераскатанным материалом → fetch отдал бы воркеру ключ, которого
    # на боксе нет (потеря управляющего доступа). FOR UPDATE сериализует: вторая
    # ротация ждёт коммит первой и видит pending_apply=True → 409 ниже.
    db.expire(server)
    locked = await server_repo.get_for_update(db, server_id)
    if locked is None:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "vanished"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    server = locked

    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept worker operations",
        )

    # Ротация перевыпускает SSH-ключ на боксе — деструктивно для активной
    # управляющей сессии. Пока сервер `updating` (astra-update в полёте) или
    # занят под чужого — не ротируем: тот же гейт, что у prepare/astra_update.
    reservation.ensure_not_reserved_for(identity, server, action=audit_action)
    reservation.ensure_not_acs_locked(identity, server)

    # Ротация имеет смысл только на подготовленном сервере: на неуправляемом
    # ещё нет ни ключа, ни управляющего пользователя — сначала prepare.
    if not server.is_managed:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "prepare_required", "department_id": server.department_id},
        )
        raise ConflictError(
            error_code="PREPARE_REQUIRED",
            message=(
                "Server is not prepared for management; run POST "
                "/servers/{id}/prepare before rotating management credentials"
            ),
        )

    # Предыдущая ротация ещё висит неподтверждённой — новую не запускаем.
    # Иначе перенос `mgmt_*`→`previous_*` затёр бы реально стоящий на боксе
    # ключ не установленным материалом (см. rotate_management_credentials).
    if server.mgmt_creds_pending_apply:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "rotation_pending", "department_id": server.department_id},
        )
        raise ConflictError(
            error_code="MGMT_ROTATION_PENDING",
            message=(
                "Management credentials rotation is already in progress or "
                "stuck (pending apply). Wait for it to be applied on the box "
                "or reset it before starting a new rotation."
            ),
        )

    idempotency_key = read_idempotency_key(request)

    # Генерация нового материала + dispatch в одном savepoint'е: либо коммитим
    # обе мутации после успешного dispatch'а, либо роллбэчим. Свежий ciphertext
    # без доехавшей задачи оставил бы pending_apply=True навсегда и разъезд
    # БД↔бокс. До подтверждения fetch отдаёт previous, поэтому SSH не рвётся.
    creds_sp = await db.begin_nested()
    new_creds = await management_creds_svc.rotate_management_credentials(db, server)
    stash_key = worker_client.dispatch_creds_key(dispatch_creds_id())
    try:
        await worker_client.store_dispatch_creds(
            stash_key,
            {
                "new_public_key": new_creds["public_key"],
                "new_private_key": new_creds["private_key"],
                "new_password": new_creds["password"],
                "management_user": server.management_user,
            },
        )
    except ServiceUnavailableError:
        await creds_sp.rollback()
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "creds_store_unavailable",
                "task_kind": task_kind,
                "department_id": server.department_id,
            },
        )
        raise
    except Exception as exc:
        await worker_client.delete_dispatch_creds(stash_key)
        await creds_sp.rollback()
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "creds_store_failed",
                "task_kind": task_kind,
                "department_id": server.department_id,
                "error_class": type(exc).__name__,
            },
        )
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_UNAVAILABLE",
            message="Failed to stash rotation credentials before dispatch",
        ) from exc

    payload: dict = {
        "server_id": server.id,
        "target_department_id": server.department_id,
        # SSH — по IP, не по hostname (короткие имена не резолвятся из пода).
        "host": str(server.ip_address),
        "ssh_port": server.ssh_port,
        "is_managed": server.is_managed,
        "management_user": server.management_user,
        "creds_stash_key": stash_key,
    }
    dispatch_ok = False
    idempotent_hit = False
    try:
        try:
            task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
                db=db,
                task_kind=task_kind,
                target_server_id=server.id,
                payload=payload,
                created_by=identity.user_id,
                request_id=getattr(request.state, "request_id", None),
                idempotency_key=idempotency_key,
            )
            dispatch_ok = True
        except ConflictError:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": "idempotent_conflict",
                    "task_kind": task_kind,
                    "department_id": server.department_id,
                },
            )
            raise
        except ServiceUnavailableError:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": "worker_unreachable",
                    "task_kind": task_kind,
                    "department_id": server.department_id,
                },
            )
            raise
    finally:
        if not dispatch_ok:
            await worker_client.delete_dispatch_creds(stash_key)
            await creds_sp.rollback()
    # Idempotent-hit на race-пути: оригинальная task несёт свой stash, нашу
    # свежую ротацию коммитить нельзя (БД хранила бы NEW, бокс — OLD).
    if idempotent_hit:
        await worker_client.delete_dispatch_creds(stash_key)
        await creds_sp.rollback()
    else:
        await creds_sp.commit()
    await db.commit()
    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": task_kind,
            "department_id": server.department_id,
            "idempotent_hit": idempotent_hit,
        },
    )
    return ServerManagementCredsRotateResponse(task_id=task_id, status="queued")


# ── /servers/{id}/clean — оркестрация очистки после переустановки ОС ────────


@router_servers.post(
    "/clean",
    response_model=ServerCleanResponse,
    status_code=202,
    summary="Очистить сервер после переустановки ОС (оркестрация, 202)",
    description=(
        "Оркестрирует выбранные в модалке действия в фиксированном порядке: "
        "① `unbind_accounts` → ③ `rerun_prepare` → ② `update_os_version` → "
        "④ `run_inventory_sync` → ⑤ `delete_vms`. Каждое действие переиспользует "
        "существующий путь:\n\n"
        "* `unbind_accounts` — отвязать ВСЕ привязанные учётки сервера (снять "
        "связки + userdel-fanout на боксы, где учётка стояла);\n"
        "* `rerun_prepare` — `server.prepare` с bootstrap-кредами из блока "
        "`prepare` (account-режим по умолчанию, опц. ручной): пере-создаёт "
        "управляющую учётку и ре-провижнит привязанные аккаунты;\n"
        "* `update_os_version` — ручной os-sync (`os_version_id`, `null` "
        "сбрасывает);\n"
        "* `run_inventory_sync` — `inventory.sync` (требует prepared-сервер);\n"
        "* `delete_vms` — снести все ВМ этого хаба (переустановка ОС стёрла их "
        "qcow2-диски): row-only каскад строк, без диспатча на хаб.\n\n"
        "Порядок фиксированный: если выбраны и `unbind_accounts`, и "
        "`rerun_prepare` — после unbind привязанных учёток не остаётся, prepare "
        "пере-создаст только управляющую (как «только поставили ОС»).\n\n"
        "Ответ — per-action сводка `{status, task_id?, reason?, detail?}`: "
        "`done` (sync-действие выполнено), `dispatched` (task поставлена), "
        "`skipped` (`reason=not_selected`), `failed` (`reason` несёт причину). "
        "Доступ: `(server, *, update)` — как у prepare. Аудит — `server.clean` "
        "CRITICAL + по-действенные существующие эмиты."
    ),
    responses={
        202: {"description": "Clean принят; per-action сводка в теле."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `update` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404)."},
        409: {"description": "SERVER_DECOMMISSIONED."},
        422: {"description": "Ни одного действия не выбрано / rerun_prepare без `prepare` / битый base64 / слабый пароль."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP clean-rate-limit пробит."},
    },
)
@endpoint_limiter.limit(get_settings().server_prepare_rate_limit)
async def server_clean_dispatch(
    request: Request,
    server_id: str,
    body: ServerCleanRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerCleanResponse:
    """Оркестрирует очистку сервера после переустановки ОС.

    Доступ: `(server, *, update)`. Выбранные флаги выполняются в порядке
    unbind → prepare → os-version → inventory; каждое действие переиспользует
    существующий путь и отдаёт per-action итог. Связано:
    `server_account.unbind_all_accounts_from_server`,
    `_prepare_resolve_and_dispatch`, `server.update_os_version`,
    `_dispatch_for_server`.
    """
    audit_action = "server.clean"

    # Permission → visibility → decommissioned, тот же порядок, что у prepare.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.UPDATE
        )

    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise

    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept worker operations",
        )

    request_id = getattr(request.state, "request_id", None)
    # Каждое действие коммитит транзакцию и экспайрит ORM-атрибуты `server`.
    # Фиксируем department_id, пока строка свежая, — нужен для финального
    # аудита (после последнего коммита ленивый reload в async упал бы).
    server_department_id = server.department_id

    # Невыбранное действие отдаётся как skipped/not_selected — UI видит явный
    # исход по каждому из четырёх флагов, а не отсутствие ключа.
    def _not_selected() -> ServerCleanActionResult:
        return ServerCleanActionResult(status="skipped", reason="not_selected")

    unbind_res = _not_selected()
    prepare_res = _not_selected()
    os_res = _not_selected()
    inv_res = _not_selected()
    delvm_res = _not_selected()

    # ① unbind_accounts — отвязать все учётки + userdel-fanout.
    if body.unbind_accounts:
        detail = await account_svc.unbind_all_accounts_from_server(
            db, identity, server, request_id=request_id,
        )
        unbind_res = ServerCleanActionResult(status="done", detail=detail)

    # ③ rerun_prepare — пере-бутстрап управления.
    if body.rerun_prepare:
        # unbind мог закоммитить и проэкспайрить `server` — перечитываем строку,
        # прежде чем `_prepare_resolve_and_dispatch` начнёт читать её атрибуты.
        await db.refresh(server)
        resolve_creds = _make_prepare_creds_resolver(
            db=db, identity=identity, prepare_req=body.prepare,
            server_id=server_id, audit_action="server.prepare",
        )
        try:
            task_id = await _prepare_resolve_and_dispatch(
                db=db, identity=identity, request=request,
                server=server, audit_action="server.prepare",
                task_kind="server.prepare", resolve_creds=resolve_creds,
            )
            prepare_res = ServerCleanActionResult(status="dispatched", task_id=task_id)
        except AuthorizationError:
            prepare_res = ServerCleanActionResult(status="failed", reason="permission_denied")
        except (NotFoundError, ConflictError) as exc:
            prepare_res = ServerCleanActionResult(status="failed", reason=exc.error_code.lower())
        except ServiceUnavailableError:
            prepare_res = ServerCleanActionResult(status="failed", reason="worker_unreachable")

    # ② update_os_version — ручной os-sync (переиспользует server.update_os_version).
    if body.update_os_version:
        try:
            await server_svc.update_os_version(
                db, identity, server_id,
                ServerOsVersionUpdate(os_version_id=body.os_version_id),
            )
            os_res = ServerCleanActionResult(
                status="done", detail={"os_version_id": body.os_version_id},
            )
        except AuthorizationError:
            os_res = ServerCleanActionResult(status="failed", reason="permission_denied")
        except (NotFoundError, ConflictError, DomainValidationError) as exc:
            os_res = ServerCleanActionResult(status="failed", reason=exc.error_code.lower())

    # ④ run_inventory_sync — SSH-probe (требует prepared-сервер).
    if body.run_inventory_sync:
        try:
            result = await _dispatch_for_server(
                db=db, identity=identity, request=request,
                server_id=server_id,
                action=Action.INVENTORY_TRIGGER,
                audit_action="server.inventory_sync",
                task_kind="inventory.sync",
                require_ipmi=False,
                require_prepared=True,
            )
            inv_res = ServerCleanActionResult(status="dispatched", task_id=result["task_id"])
        except AuthorizationError:
            inv_res = ServerCleanActionResult(status="failed", reason="permission_denied")
        except (NotFoundError, ConflictError) as exc:
            inv_res = ServerCleanActionResult(status="failed", reason=exc.error_code.lower())
        except ServiceUnavailableError:
            inv_res = ServerCleanActionResult(status="failed", reason="worker_unreachable")

    # ⑤ delete_vms — снести orphan-ВМ хаба (диски стёрты переустановкой ОС).
    # Идёт последним: row-only каскад с собственным commit'ом, ничьи атрибуты
    # `server` дальше не читаем (кроме PK).
    if body.delete_vms:
        try:
            detail = await vm_svc.delete_vms_for_clean(
                db, server, request_id=request_id,
            )
            delvm_res = ServerCleanActionResult(status="done", detail=detail)
        except (NotFoundError, ConflictError) as exc:
            delvm_res = ServerCleanActionResult(
                status="failed", reason=exc.error_code.lower(),
            )

    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "department_id": server_department_id,
            "actions": {
                "unbind_accounts": body.unbind_accounts,
                "rerun_prepare": body.rerun_prepare,
                "update_os_version": body.update_os_version,
                "run_inventory_sync": body.run_inventory_sync,
                "delete_vms": body.delete_vms,
            },
            "results": {
                "unbind_accounts": unbind_res.status,
                "rerun_prepare": prepare_res.status,
                "update_os_version": os_res.status,
                "run_inventory_sync": inv_res.status,
                "delete_vms": delvm_res.status,
            },
        },
    )
    return ServerCleanResponse(
        server_id=server_id,
        unbind_accounts=unbind_res,
        rerun_prepare=prepare_res,
        update_os_version=os_res,
        run_inventory_sync=inv_res,
        delete_vms=delvm_res,
    )


# ── /servers/prepare/bulk — массовый онбординг ──────────────────────────────


router_servers_bulk = APIRouter(prefix="/servers")


@router_servers_bulk.post(
    "/prepare/bulk",
    response_model=ServerPrepareBulkResponse,
    status_code=202,
    summary="Массовый бутстрап управления серверами через worker (202)",
    description=(
        "Публикует `server.prepare` на список серверов — по задаче на сервер, "
        "у каждого свои bootstrap-креды (`username_b64`/`password_b64` плюс "
        "опциональный `ssh_private_key_b64`). Те же гейты, что у single "
        "`POST /servers/{id}/prepare`: право `(server, *, update)`, visibility/"
        "dept-isolation, decommissioned-check, Redis-stash bootstrap-кред, "
        "dispatch.\n\n"
        "Per-server результат: `{server_id, status, task_id?, reason?}`. Один "
        "битый сервер (cross-dept / списан / уже-в-очереди) уходит в `skipped` "
        "с reason и НЕ валит остальной батч. Глобальная недоступность воркера "
        "(redis down) на первом же сервере отбивает весь запрос 503. Дубли "
        "server_id в теле → 422. Число серверов > `BULK_PREPARE_MAX_SERVERS` → "
        "413. Аудит CRITICAL на каждый сервер, как в single-prepare."
    ),
    responses={
        202: {"description": "Батч принят; per-server results в теле ответа."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `update` — весь батч отбит (право не зависит от сервера)."},
        413: {"description": "BULK_PREPARE_TOO_LARGE — батч превысил BULK_PREPARE_MAX_SERVERS."},
        422: {"description": "Битый base64 / слабый пароль / дубли server_id."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP bulk-prepare-rate-limit пробит."},
        503: {"description": "Worker недоступен на первом сервере (redis down / не сконфигурён)."},
    },
)
@endpoint_limiter.limit(get_settings().bulk_prepare_rate_limit)
async def server_prepare_bulk_dispatch(
    request: Request,
    body: ServerPrepareBulkRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerPrepareBulkResponse:
    """Ставит `server.prepare` на список серверов — по задаче на сервер.

    Доступ: `(server, *, update)`. Битый base64 / слабый пароль / дубли
    server_id → 422 (валидатор схемы срабатывает до этого хендлера). Связано:
    `_prepare_resolve_and_dispatch`, `server_prepare_dispatch` (single).
    """
    audit_action = "server.prepare"
    task_kind = "server.prepare"

    # Cap на размер батча: один запрос не вправе шедулить произвольное число
    # prepare-задач (каждая кладёт plaintext-bootstrap-креды в Redis под TTL).
    cap = get_settings().bulk_prepare_max_servers
    if len(body.items) > cap:
        audit_service.emit(
            audit_action, target_id=None, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "bulk_prepare_too_large",
                "count": len(body.items),
                "cap": cap,
            },
        )
        raise AppException(
            error_code="BULK_PREPARE_TOO_LARGE",
            message=(
                f"Bulk prepare batch of {len(body.items)} servers exceeds cap "
                f"{cap}; split into smaller batches"
            ),
            http_status=413,
        )

    # Право `update` зависит только от ролей caller'а, не от конкретного
    # сервера — проверяем один раз на весь батч (как permission-first шаг в
    # single-prepare). Нет права → 403 на весь запрос, ни одной задачи.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=None,
        target_type="server",
        extra_details={"operation": "prepare_bulk", "count": len(body.items)},
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, Action.UPDATE)

    # Batch-load видимых серверов одним `WHERE id IN (...)`: cross-dept и
    # отсутствующие просто не попадают в map (→ skipped), без N round-trip'ов.
    item_by_id = {it.server_id: it for it in body.items}
    servers_by_id = await server_svc.load_visible_servers(
        db, identity, list(item_by_id.keys()),
    )

    results: list[ServerPrepareBulkResult] = []
    for item in body.items:
        sid = item.server_id
        server = servers_by_id.get(sid)
        if server is None:
            audit_service.emit(
                audit_action, target_id=sid, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept", "operation": "prepare_bulk"},
            )
            results.append(ServerPrepareBulkResult(
                server_id=sid, status="skipped", reason="not_found_or_cross_dept",
            ))
            continue

        async def resolve_creds(srv, _item=item) -> dict:
            # Bulk — только ручной режим: у каждого бокса свои bootstrap-креды.
            creds: dict = {
                "bootstrap_login": _item.username(),
                "bootstrap_password": _item.password(),
            }
            ssh_private_key = _item.ssh_private_key()
            if ssh_private_key is not None:
                creds["bootstrap_ssh_private_key"] = ssh_private_key
            return creds

        try:
            task_id = await _prepare_resolve_and_dispatch(
                db=db, identity=identity, request=request,
                server=server, audit_action=audit_action, task_kind=task_kind,
                resolve_creds=resolve_creds,
            )
        except ConflictError as exc:
            # Списанный / idempotent / reuse-конфликт — per-server, в skipped.
            # Один битый сервер не валит весь батч.
            reason = {
                "SERVER_DECOMMISSIONED": "decommissioned",
                "IDEMPOTENCY_KEY_REUSE_CONFLICT": "idempotency_key_reuse_conflict",
            }.get(exc.error_code, "idempotent_conflict")
            results.append(ServerPrepareBulkResult(
                server_id=sid, status="skipped", reason=reason,
            ))
            continue
        except ServiceUnavailableError:
            # Воркер недоступен глобально (redis down / не сконфигурён) — не
            # per-server проблема: продолжать батч бессмысленно, каждый
            # следующий сервер упадёт идентично. Already-queued уже в results;
            # пробрасываем 503, дописывать остаток в skipped не имеет смысла —
            # клиент ретраит весь батч (idempotent по уже-поставленным).
            audit_service.emit(
                audit_action, target_id=sid, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": "worker_unreachable_bulk_abort",
                    "operation": "prepare_bulk",
                    "queued_count": sum(1 for r in results if r.status == "queued"),
                },
            )
            raise
        results.append(ServerPrepareBulkResult(
            server_id=sid, status="queued", task_id=task_id,
        ))

    queued = sum(1 for r in results if r.status == "queued")
    skipped = sum(1 for r in results if r.status == "skipped")
    audit_service.emit(
        audit_action, target_id=None, target_type="server",
        status="success", allowed=True,
        details={
            "operation": "prepare_bulk",
            "task_kind": task_kind,
            "queued_count": queued,
            "skipped_count": skipped,
            "server_ids": [r.server_id for r in results],
        },
    )
    return ServerPrepareBulkResponse(
        results=results, queued_count=queued, skipped_count=skipped,
    )


# ── /servers/prepare-batch — массовый онбординг с per-server режимом кред ────


@router_servers_bulk.post(
    "/prepare-batch",
    response_model=ServerPrepareBatchResponse,
    status_code=202,
    summary="Массовый бутстрап управления с per-server выбором кред (202)",
    description=(
        "Публикует `server.prepare` на список серверов — по задаче на сервер. "
        "Для КАЖДОГО сервера свой режим bootstrap-кред (как у single-prepare): "
        "`account_id` (привязанная учётка, по умолчанию) либо ручной "
        "`username_b64`/`password_b64`(+`ssh_private_key_b64`). Те же гейты, что "
        "у single `POST /servers/{id}/prepare`: право `(server, *, update)` "
        "(один раз на весь батч), visibility/dept-isolation, decommissioned-"
        "check, Redis-stash bootstrap-кред, dispatch. account-режим дополнительно "
        "требует `view_password` на конкретную учётку.\n\n"
        "Ответ симметричен mass-rotation: `{batch_id, dispatched, failed}`. "
        "`dispatched` — `{server_id, server_name, task_id, status}`; `failed` — "
        "`{server_id, server_name, reason}`. Один битый сервер (cross-dept / "
        "списан / уже-в-очереди / нет права на учётку / нет пароля) уходит в "
        "`failed` и НЕ валит остальной батч. Глобальная недоступность воркера "
        "помечает упавший сервер `worker_unreachable`, остаток — `not_attempted`. "
        "Дубли server_id → 422. Число серверов > `BULK_PREPARE_MAX_SERVERS` → 413. "
        "Аудит CRITICAL на каждый сервер, как в single-prepare."
    ),
    responses={
        202: {"description": "Батч принят; per-server dispatched/failed в теле."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `update` — весь батч отбит."},
        413: {"description": "BULK_PREPARE_TOO_LARGE — батч превысил BULK_PREPARE_MAX_SERVERS."},
        422: {"description": "Битый base64 / слабый пароль / нарушение режима / дубли server_id."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP batch-prepare-rate-limit пробит."},
    },
)
@endpoint_limiter.limit(get_settings().bulk_prepare_rate_limit)
async def server_prepare_batch_dispatch(
    request: Request,
    body: ServerPrepareBatchRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerPrepareBatchResponse:
    """Ставит `server.prepare` на список серверов с per-server режимом кред.

    Доступ: `(server, *, update)`. Битый base64 / слабый пароль / нарушение
    режима / дубли server_id → 422 (валидатор схемы). Связано:
    `_make_prepare_creds_resolver`, `_prepare_resolve_and_dispatch`.
    """
    audit_action = "server.prepare"
    task_kind = "server.prepare"

    cap = get_settings().bulk_prepare_max_servers
    if len(body.items) > cap:
        audit_service.emit(
            audit_action, target_id=None, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "bulk_prepare_too_large",
                "operation": "prepare_batch",
                "count": len(body.items),
                "cap": cap,
            },
        )
        raise AppException(
            error_code="BULK_PREPARE_TOO_LARGE",
            message=(
                f"Prepare batch of {len(body.items)} servers exceeds cap "
                f"{cap}; split into smaller batches"
            ),
            http_status=413,
        )

    with emit_denied_on_authz_error(
        audit_action,
        target_id=None,
        target_type="server",
        extra_details={"operation": "prepare_batch", "count": len(body.items)},
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, Action.UPDATE)

    item_by_id = {it.server_id: it for it in body.items}
    servers_by_id = await server_svc.load_visible_servers(
        db, identity, list(item_by_id.keys()),
    )

    batch_id = rotation_batch_id()
    dispatched: list[dict] = []
    failed: list[dict] = []
    aborted = False
    for item in body.items:
        sid = item.server_id
        # Воркер уже отбил ServiceUnavailable глобально — остаток не пытаемся,
        # каждый следующий упал бы идентично. Помечаем not_attempted.
        if aborted:
            failed.append({"server_id": sid, "server_name": None, "reason": "not_attempted"})
            continue
        server = servers_by_id.get(sid)
        if server is None:
            audit_service.emit(
                audit_action, target_id=sid, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept", "operation": "prepare_batch"},
            )
            failed.append({"server_id": sid, "server_name": None, "reason": "not_found_or_cross_dept"})
            continue
        resolve_creds = _make_prepare_creds_resolver(
            db=db, identity=identity, prepare_req=item,
            server_id=sid, audit_action=audit_action,
        )
        try:
            task_id = await _prepare_resolve_and_dispatch(
                db=db, identity=identity, request=request,
                server=server, audit_action=audit_action, task_kind=task_kind,
                resolve_creds=resolve_creds,
            )
        except ConflictError as exc:
            reason = {
                "SERVER_DECOMMISSIONED": "decommissioned",
                "IDEMPOTENCY_KEY_REUSE_CONFLICT": "idempotency_key_reuse_conflict",
                "ACCOUNT_HAS_NO_PASSWORD": "account_has_no_password",
            }.get(exc.error_code, "idempotent_conflict")
            failed.append({"server_id": sid, "server_name": _server_name(server), "reason": reason})
            continue
        except AuthorizationError:
            # account-режим без `view_password` на конкретную учётку.
            failed.append({"server_id": sid, "server_name": _server_name(server), "reason": "permission_denied"})
            continue
        except NotFoundError as exc:
            # account-режим: учётка не видна / не привязана к серверу.
            failed.append({
                "server_id": sid, "server_name": _server_name(server),
                "reason": exc.error_code.lower(),
            })
            continue
        except ServiceUnavailableError:
            audit_service.emit(
                audit_action, target_id=sid, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": "worker_unreachable_batch_abort",
                    "operation": "prepare_batch",
                    "dispatched_count": len(dispatched),
                },
            )
            failed.append({"server_id": sid, "server_name": _server_name(server), "reason": "worker_unreachable"})
            aborted = True
            continue
        dispatched.append({"server_id": sid, "server_name": _server_name(server), "task_id": task_id})

    audit_service.emit(
        audit_action, target_id=None, target_type="server",
        status="success", allowed=True,
        details={
            "operation": "prepare_batch",
            "task_kind": task_kind,
            "batch_id": batch_id,
            "dispatched_count": len(dispatched),
            "failed_count": len(failed),
            "server_ids": [it.server_id for it in body.items],
        },
    )
    return ServerPrepareBatchResponse(
        batch_id=batch_id,
        dispatched=[ServerBatchDispatched(**d) for d in dispatched],
        failed=[ServerBatchFailed(**f) for f in failed],
    )


# ── /servers/acs-snapshots/{create,restore}-batch — массовые ACS-операции ───


_ACS_BATCH_CONFLICT_REASONS: dict[str, str] = {
    "SERVER_DECOMMISSIONED": "decommissioned",
    "SERVER_IS_VMS_HUB": "server_is_vms_hub",
    "SERVER_RESERVED": "reserved",
    "SERVER_UPDATING": "updating",
    "SERVER_ACS_BUSY": "acs_busy",
    "PREPARE_REQUIRED": "prepare_required",
    "ACS_BOOTSTRAP_PASSWORD_MISSING": "bootstrap_password_missing",
}


async def _acs_batch_dispatch(
    *,
    db: AsyncSession,
    identity,
    request: Request,
    server_ids: list[str],
    audit_action: str,
    task_kind: str,
    os_version_id: str,
    require_prepared: bool,
    require_bootstrap_password: bool,
    busy_note_action: str,
) -> tuple[list[dict], list[dict]]:
    """Общий цикл batch create/restore ACS по списку `server_id`.

    Переиспользует `_acs_resolve_and_dispatch` — тот же гейт-набор, что у
    single-dispatch. `Action.ACS_SNAPSHOT` — один тип-wide action на
    list/create/restore, `require_action` уже прошёл один раз до вызова этой
    функции (и для create, и для restore) — per-server permission-check тут
    не нужен.

    Глобальные срывы (`ACS_DISABLED`, воркер недоступен) абортят остаток
    батча в `not_attempted` — следующий сервер упал бы идентично. Per-server
    гейты (decommissioned / vms-hub / acs-busy / reserved / updating /
    prepared / bootstrap-пароль) уходят в `failed`, не валя остальной батч.
    """
    servers_by_id = await server_svc.load_visible_servers(db, identity, server_ids)
    dispatched: list[dict] = []
    failed: list[dict] = []
    aborted = False
    for sid in server_ids:
        if aborted:
            failed.append({"server_id": sid, "server_name": None, "reason": "not_attempted"})
            continue
        server = servers_by_id.get(sid)
        if server is None:
            audit_service.emit(
                audit_action, target_id=sid, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept", "task_kind": task_kind},
            )
            failed.append({"server_id": sid, "server_name": None, "reason": "not_found_or_cross_dept"})
            continue

        try:
            task_id = await _acs_resolve_and_dispatch(
                db=db, identity=identity, request=request, server=server,
                audit_action=audit_action, task_kind=task_kind,
                os_version_id=os_version_id,
                require_prepared=require_prepared,
                require_bootstrap_password=require_bootstrap_password,
                busy_note_action=busy_note_action,
            )
        except ConflictError as exc:
            reason = _ACS_BATCH_CONFLICT_REASONS.get(exc.error_code, "idempotent_conflict")
            failed.append({"server_id": sid, "server_name": _server_name(server), "reason": reason})
            continue
        except AuthorizationError as exc:
            reason = (
                "acs_department_not_enabled"
                if exc.error_code == "ACS_DEPARTMENT_NOT_ENABLED"
                else "permission_denied"
            )
            failed.append({"server_id": sid, "server_name": _server_name(server), "reason": reason})
            continue
        except NotFoundError as exc:
            failed.append({
                "server_id": sid, "server_name": _server_name(server),
                "reason": exc.error_code.lower(),
            })
            continue
        except ServiceUnavailableError as exc:
            reason = "acs_disabled" if exc.error_code == "ACS_DISABLED" else "worker_unreachable"
            audit_service.emit(
                audit_action, target_id=sid, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": f"{reason}_batch_abort",
                    "task_kind": task_kind,
                    "dispatched_count": len(dispatched),
                },
            )
            failed.append({"server_id": sid, "server_name": _server_name(server), "reason": reason})
            aborted = True
            continue

        dispatched.append({"server_id": sid, "server_name": _server_name(server), "task_id": task_id})

    return dispatched, failed


def _check_acs_batch_cap(*, audit_action: str, operation: str, count: int) -> None:
    """Cap на размер ACS-батча — переиспользует лимит `bulk_prepare_max_servers`.

    Отдельного `ACS_SNAPSHOT_BATCH_MAX_SERVERS` не заводим: нагрузка на воркер
    от одной ACS-задачи (SSH-probe окно 10-30 минут) сопоставима с prepare, тот
    же порядок величины cap'а подходит без нового конфига.
    """
    cap = get_settings().bulk_prepare_max_servers
    if count <= cap:
        return
    audit_service.emit(
        audit_action, target_id=None, target_type="server",
        status="failure", allowed=True,
        details={
            "reason": "acs_snapshot_batch_too_large",
            "operation": operation,
            "count": count,
            "cap": cap,
        },
    )
    raise AppException(
        error_code="ACS_SNAPSHOT_BATCH_TOO_LARGE",
        message=(
            f"ACS snapshot batch of {count} servers exceeds cap {cap}; "
            f"split into smaller batches"
        ),
        http_status=413,
    )


@router_servers_bulk.post(
    "/acs-snapshots/create-batch",
    response_model=ServerAcsSnapshotBatchResponse,
    status_code=202,
    summary="Массовое создание снимков ACS по списку серверов (202)",
    description=(
        "Ставит `acs.snapshot_create` на список серверов — по задаче на "
        "сервер, одна версия каталога ОС на весь батч. `Action.ACS_SNAPSHOT` — "
        "тип-wide действие (`_NON_INSTANCE_ACTIONS`), право проверяется ОДИН "
        "раз на весь батч, не per-server (симметрично prepare-batch с "
        "`update`). Остальные гейты — те же, что у single-dispatch (`POST "
        "/servers/{id}/acs-snapshots`): decommissioned, VMS-hub, ACS-busy, "
        "reservation, prepared-gate, ACS-доступность (platform+department), "
        "каталог-версия. Один битый сервер уходит в `failed` и НЕ валит "
        "остальной батч; глобальная недоступность ACS/воркера помечает "
        "упавший сервер причиной, остаток — `not_attempted`. Дубли "
        "`server_id` → 422, размер батча > `bulk_prepare_max_servers` → 413 "
        "`ACS_SNAPSHOT_BATCH_TOO_LARGE`."
    ),
    responses={
        202: {"description": "Батч принят; per-server dispatched/failed в теле."},
        403: {"description": "Нет `acs_snapshot`."},
        413: {"description": "ACS_SNAPSHOT_BATCH_TOO_LARGE — батч превысил cap."},
        422: {"description": "Дубли server_id в теле."},
    },
)
async def server_acs_snapshot_create_batch_dispatch(
    request: Request,
    body: ServerAcsSnapshotBatchRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAcsSnapshotBatchResponse:
    """Массовый `acs.snapshot_create` — право на весь батч + dispatch.

    Связано: `_acs_batch_dispatch`, `_acs_resolve_and_dispatch`,
    `server_acs_snapshot_create_dispatch` (single).
    """
    audit_action = "server.acs_snapshot_create"
    task_kind = "acs.snapshot_create"

    with emit_denied_on_authz_error(
        audit_action, target_id=None, target_type="server",
        extra_details={"operation": "acs_snapshot_create_batch"},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.ACS_SNAPSHOT
        )

    _check_acs_batch_cap(
        audit_action=audit_action, operation="acs_snapshot_create_batch",
        count=len(body.server_ids),
    )

    batch_id = rotation_batch_id()
    dispatched, failed = await _acs_batch_dispatch(
        db=db, identity=identity, request=request,
        server_ids=body.server_ids,
        audit_action=audit_action, task_kind=task_kind,
        os_version_id=body.os_version_id,
        require_prepared=True,
        require_bootstrap_password=False,
        busy_note_action="save",
    )

    audit_service.emit(
        audit_action, target_id=None, target_type="server",
        status="success", allowed=True,
        details={
            "operation": "acs_snapshot_create_batch",
            "task_kind": task_kind,
            "batch_id": batch_id,
            "dispatched_count": len(dispatched),
            "failed_count": len(failed),
            "server_ids": body.server_ids,
        },
    )
    return ServerAcsSnapshotBatchResponse(
        batch_id=batch_id,
        dispatched=[ServerBatchDispatched(**d) for d in dispatched],
        failed=[ServerBatchFailed(**f) for f in failed],
    )


@router_servers_bulk.post(
    "/acs-snapshots/restore-batch",
    response_model=ServerAcsSnapshotBatchResponse,
    status_code=202,
    summary="Массовое восстановление серверов из снимков ACS (202)",
    description=(
        "Ставит `acs.snapshot_restore` на список серверов — по задаче на "
        "сервер, одна версия каталога ОС (снимка) на весь батч. Необратимо "
        "для каждого сервера в списке. `Action.ACS_SNAPSHOT` — тип-wide "
        "действие (`_NON_INSTANCE_ACTIONS`), право проверяется ОДИН раз на "
        "весь батч, не per-server (симметрично prepare-batch с `update`). "
        "Остальные гейты — те же, что у single-dispatch (`POST "
        "/servers/{id}/acs-snapshots/restore`): decommissioned, VMS-hub, "
        "ACS-busy, reservation, ACS-доступность, каталог-версия, bootstrap-"
        "пароль версии. Один битый сервер уходит в `failed` и НЕ валит "
        "остальной батч; глобальная недоступность ACS/воркера помечает "
        "упавший сервер причиной, остаток — `not_attempted`. Дубли "
        "`server_id` → 422, размер батча > `bulk_prepare_max_servers` → 413 "
        "`ACS_SNAPSHOT_BATCH_TOO_LARGE`."
    ),
    responses={
        202: {"description": "Батч принят; per-server dispatched/failed в теле."},
        403: {"description": "Нет тип-wide `acs_snapshot` — весь батч отбит."},
        413: {"description": "ACS_SNAPSHOT_BATCH_TOO_LARGE — батч превысил cap."},
        422: {"description": "Дубли server_id в теле."},
    },
)
async def server_acs_snapshot_restore_batch_dispatch(
    request: Request,
    body: ServerAcsSnapshotBatchRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerAcsSnapshotBatchResponse:
    """Массовый `acs.snapshot_restore` — один type-wide permission-check + dispatch.

    Связано: `_acs_batch_dispatch`, `_acs_resolve_and_dispatch`,
    `server_acs_snapshot_restore_dispatch` (single).
    """
    audit_action = "server.acs_snapshot_restore"
    task_kind = "acs.snapshot_restore"

    _check_acs_batch_cap(
        audit_action=audit_action, operation="acs_snapshot_restore_batch",
        count=len(body.server_ids),
    )

    with emit_denied_on_authz_error(
        audit_action,
        target_id=None,
        target_type="server",
        extra_details={
            "operation": "acs_snapshot_restore_batch",
            "count": len(body.server_ids),
        },
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.ACS_SNAPSHOT
        )

    batch_id = rotation_batch_id()
    dispatched, failed = await _acs_batch_dispatch(
        db=db, identity=identity, request=request,
        server_ids=body.server_ids,
        audit_action=audit_action, task_kind=task_kind,
        os_version_id=body.os_version_id,
        require_prepared=False,
        require_bootstrap_password=True,
        busy_note_action="restore",
    )

    audit_service.emit(
        audit_action, target_id=None, target_type="server",
        status="success", allowed=True,
        details={
            "operation": "acs_snapshot_restore_batch",
            "task_kind": task_kind,
            "batch_id": batch_id,
            "dispatched_count": len(dispatched),
            "failed_count": len(failed),
            "server_ids": body.server_ids,
        },
    )
    return ServerAcsSnapshotBatchResponse(
        batch_id=batch_id,
        dispatched=[ServerBatchDispatched(**d) for d in dispatched],
        failed=[ServerBatchFailed(**f) for f in failed],
    )


# ── /server-accounts/{id}/rotate — admin-initiated worker rotation ──────────


@router_accounts.post(
    "/rotate",
    response_model=AccountRotateDispatchResponse,
    summary="Ротация общего пароля аккаунта через worker (SSH apply + storage)",
    status_code=202,
    description=(
        "Публикует задачи `account.rotate_password`. Worker сгенерит новый "
        "пароль, применит через SSH (`chpasswd`) и POST'нет обратно в "
        "server_service internal endpoint, который зашифрует и сохранит. "
        "Пароль общий на все привязанные серверы.\n\n"
        "Два режима:\n"
        "* **точечная** — query `server_id=<srv>` указывает один из "
        "привязанных серверов; задача ставится только на него. Списанный "
        "сервер или idempotent-конфликт → 409;\n"
        "* **массовая** — без `server_id`; задача ставится на каждый "
        "привязанный сервер (по таске на сервер). Списанные серверы и "
        "уже-в-очереди (idempotent) пропускаются и попадают в `skipped`, "
        "остальные обрабатываются — один битый сервер не валит весь батч. "
        "Если списаны ВСЕ привязанные серверы → 409. Глобальная "
        "недоступность воркера (redis down) отбивает весь запрос 503. "
        "Если число пригодных к dispatch'у серверов > "
        "`MASS_ROTATION_MAX_SERVERS` → 413 MASS_ROTATION_TOO_LARGE.\n\n"
        "В отличие от `/server-accounts/{id}/rotate_password` (user-facing, "
        "меняет только запись в БД без apply'я) — этот dispatch обновляет "
        "пароль end-to-end. Plaintext клиенту не возвращается."
    ),
    responses={
        202: {"description": "Задача(и) приняты; tasks + skipped в теле ответа."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `rotate_password` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept, либо server_id не привязан."},
        409: {"description": "SERVER_DECOMMISSIONED (точечно либо все списаны) / TASK_IDEMPOTENT_CONFLICT (точечно) / IDEMPOTENCY_KEY_REUSE_CONFLICT / NO_LINKED_SERVERS (массово, аккаунт без привязок)."},
        413: {"description": "MASS_ROTATION_TOO_LARGE — батч превысил MASS_ROTATION_MAX_SERVERS."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP mass-rotate-rate-limit пробит (MASS_ROTATE_DISPATCH_RATE_LIMIT)."},
        503: {"description": "Worker недоступен (redis down / не сконфигурён)."},
    },
)
@endpoint_limiter.limit(get_settings().mass_rotate_dispatch_rate_limit)
async def account_rotate_password_dispatch(
    request: Request,
    account_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    server_id: str | None = Query(
        default=None,
        description=(
            "Точечная ротация: применить новый пароль только на этот "
            "привязанный сервер. Опущен — массовая ротация на все серверы."
        ),
    ),
) -> AccountRotateDispatchResponse:
    """Ставит `account.rotate_password` в очередь worker'а — точечно или массово.

    Доступ: `(server_account, *, rotate_password)`. Cross-dept аккаунты
    скрыты за 404 ровно как в user-facing rotate (см.
    `services/server_account.py::_load_account_visible`).

    Связано: `server_worker/src/tasks/passwords.py::account_rotate_password`,
    `server_service.internal_service` (storage round-trip).
    """
    audit_action = "server_account.rotate_password_dispatch"

    # Аддитивная авторизация (роль ИЛИ per-account грант на `rotate_password`).
    # Учётку грузим до решения: per-account грант нельзя проверить, не зная
    # конкретную учётку. Держатель бланкетной роли видит 404 на невидимую
    # цель (как раньше); без роли проходит только обладатель прямого гранта на
    # эту видимую учётку, иначе одинаковый 403 (no existence-oracle).
    has_role = await permissions.has_action(
        db, identity, EntityType.SERVER_ACCOUNT, Action.ROTATE_PASSWORD,
    )
    account = await account_repo.get_by_id(db, account_id)
    visible = account is not None and account.department_id == identity.department_id
    if has_role:
        if not visible:
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
            raise NotFoundError(
                error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
            )
    elif not (
        visible
        and await permissions.has_account_action(
            db, identity, account, Action.ROTATE_PASSWORD
        )
    ):
        details = {"reason": "permission_denied"}
        if getattr(identity, "subject_type", None) is not None:
            details["subject_type"] = identity.subject_type
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="denied", allowed=False, details=details,
        )
        raise AuthorizationError(
            error_code="PERMISSION_DENIED",
            message="No access to action 'rotate_password' on this server account",
            details={"entity_type": "server_account", "action": "rotate_password"},
        )

    linked_ids = account_repo.linked_server_ids(account)

    # Точечная: server_id обязан быть среди привязанных. Чужой/неизвестный →
    # 404 ACCOUNT_NOT_FOUND (не раскрываем, привязан ли он к другому аккаунту).
    if server_id is not None:
        if server_id not in linked_ids:
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={"reason": "server_not_linked", "server_id": server_id},
            )
            raise NotFoundError(
                error_code="ACCOUNT_NOT_FOUND",
                message="Server account not found on this server",
            )
        target_ids = [server_id]
        mode = "single"
    else:
        # Массовая ротация по аккаунту без привязок — ставить нечего.
        # Раньше отвалилось бы внизу под видом `all_targets_decommissioned`
        # (пустой dispatchable + пустой skipped), что путало оператора.
        # Отбиваем явный 409 NO_LINKED_SERVERS и пишем отдельный audit-
        # reason, чтобы SIEM не смешивал «нет привязок» с «все списаны».
        if not linked_ids:
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": "no_linked_servers",
                    "department_id": account.department_id,
                },
            )
            raise ConflictError(
                error_code="NO_LINKED_SERVERS",
                message="Account is not linked to any server; nothing to rotate",
            )
        target_ids = linked_ids
        mode = "all"

    idempotency_key = read_idempotency_key(request)
    request_id = getattr(request.state, "request_id", None)

    # Пред-флайт: грузим все целевые серверы и раскладываем на пригодные к
    # dispatch'у и decommissioned. Делаем это ДО первой постановки задачи —
    # в массовом режиме один списанный сервер не должен валить весь батч,
    # оставляя задачи 1..K-1 уже опубликованными в Redis без итогового аудита.
    #
    # Batch-load одним `WHERE id IN (...)` — раньше шёл N round-trip'ов на
    # массовой ротации с большим числом привязанных серверов.
    servers_by_id = await server_svc.load_visible_servers(db, identity, target_ids)
    dispatchable: list = []
    skipped: list[dict] = []
    for sid in target_ids:
        server = servers_by_id.get(sid)
        if server is None:
            # Сервер пропал или dept изменили out-of-band уже после линковки.
            # В точечном режиме это 404 как у `load_visible_server`.
            if mode == "single":
                raise NotFoundError(
                    error_code="SERVER_NOT_FOUND", message="Server not found",
                )
            # В массовом — пропускаем (нерабочая привязка не должна валить батч).
            skipped.append({
                "server_id": sid, "server_name": None,
                "reason": "not_found_or_cross_dept",
            })
            continue
        if server.status == ServerStatus.DECOMMISSIONED:
            if mode == "single":
                # Точечная ротация на единственный явно указанный сервер —
                # списанность это hard-fail, отбиваем 409 как раньше.
                audit_service.emit(
                    audit_action, target_id=account_id, target_type="server_account",
                    status="failure", allowed=True,
                    details={
                        "reason": "decommissioned",
                        "server_id": server.id,
                        "department_id": server.department_id,
                    },
                )
                raise ConflictError(
                    error_code="SERVER_DECOMMISSIONED",
                    message="Server is decommissioned, password rotation via worker not allowed",
                )
            skipped.append({
                "server_id": server.id, "server_name": _server_name(server),
                "reason": "decommissioned",
            })
            continue

        # Бронь и ACS-лок: apply идёт по SSH на живой хост, занятый чужим
        # владельцем или ACS-снимком сервер трогать нельзя. Точечно — hard
        # fail как decommissioned, массово — сервер уходит в skipped, а не
        # валит остальной батч.
        try:
            reservation.ensure_not_reserved_for(identity, server, action=audit_action)
            reservation.ensure_not_acs_locked(identity, server)
        except ConflictError as exc:
            if mode == "single":
                raise
            skipped.append({
                "server_id": server.id, "server_name": _server_name(server),
                "reason": exc.error_code.lower(),
            })
            continue
        dispatchable.append(server)

    # Все привязанные серверы списаны — ставить нечего, ведём себя как
    # точечный decommissioned-кейс (массовая ротация по пустому множеству
    # пригодных — это 409, а не «успешно поставлено 0 задач»).
    if not dispatchable:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "all_targets_decommissioned",
                "server_ids": [s["server_id"] for s in skipped],
                "department_id": account.department_id,
            },
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="All linked servers are decommissioned; password rotation via worker not allowed",
        )

    # Batch-cap на mode=all: один запрос не вправе шедулить произвольное число
    # task'ов. Точечный режим (mode=single) под cap не попадает: там всегда
    # ровно один target.
    cap = get_settings().mass_rotation_max_servers
    if mode == "all" and len(dispatchable) > cap:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "mass_rotation_too_large",
                "dispatchable_count": len(dispatchable),
                "cap": cap,
                "department_id": account.department_id,
            },
        )
        raise AppException(
            error_code="MASS_ROTATION_TOO_LARGE",
            message=(
                f"Mass rotation batch of {len(dispatchable)} servers exceeds "
                f"cap {cap}; split into smaller batches"
            ),
            http_status=413,
        )

    # Батч-id и name-map: per-task детали в ответе несут server_name, чтобы
    # UI показывал «ушло/упало» без отдельного lookup'а сервера.
    batch_id = rotation_batch_id()
    name_by_id = {s.id: _server_name(s) for s in dispatchable}

    tasks: list[dict] = []
    idempotent_hits: list[str] = []
    for server in dispatchable:
        # Per-server idempotency-key суффикс — иначе один Idempotency-Key на
        # массовую ротацию схлопнул бы все серверы в одну задачу.
        per_server_key = f"{idempotency_key}:{server.id}" if idempotency_key else None
        # Для chpasswd не нужны управляемые атрибуты (sudo/groups/shell/home).
        payload = _build_account_task_payload(
            server=server, account=account, include_attrs=False,
        )
        try:
            task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
                db=db,
                task_kind="account.rotate_password",
                target_server_id=server.id,
                target_resource_id=account_id,
                payload=payload,
                created_by=identity.user_id,
                request_id=request_id,
                idempotency_key=per_server_key,
            )
            await db.commit()
            if idempotent_hit:
                idempotent_hits.append(server.id)
        except ConflictError:
            # Idempotent-конфликт — per-server: на этот сервер уже стоит
            # идентичная задача. В точечном режиме это hard-fail (единственная
            # цель), в массовом — пропускаем сервер и продолжаем батч, чтобы
            # один уже-в-очереди сервер не отменял остальные.
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "reason": "idempotent_conflict",
                    "task_kind": "account.rotate_password",
                    "server_id": server.id,
                    "department_id": server.department_id,
                },
            )
            if mode == "single":
                raise
            skipped.append({
                "server_id": server.id, "server_name": _server_name(server),
                "reason": "idempotent_conflict",
            })
            continue
        except ServiceUnavailableError:
            # Воркер недоступен глобально (redis down / не сконфигурён) — не
            # per-server проблема: продолжать батч смысла нет, каждый
            # следующий сервер упадёт идентично.
            #
            # Раньше эмитили два события подряд (per-server `worker_unreachable`
            # + агрегат `worker_unreachable_partial`) — SIEM получал
            # удвоенный сигнал на одну и ту же причину. Сжали в один
            # агрегат: `reason=worker_unreachable_partial`, упавший сервер
            # выезжает в `failed`/`unreachable_count`, состав батча
            # (dispatched/not_attempted/skipped) — рядом.
            failed_server_id = server.id
            dispatched_index = dispatchable.index(server)
            not_attempted = [s.id for s in dispatchable[dispatched_index + 1:]]
            audit_service.emit(
                audit_action, target_id=account_id, target_type="server_account",
                status="failure", allowed=True,
                details={
                    "batch_id": batch_id,
                    "mode": mode,
                    "task_kind": "account.rotate_password",
                    "reason": "worker_unreachable_partial",
                    "task_ids": [t["task_id"] for t in tasks],
                    "dispatched": [t["server_id"] for t in tasks],
                    "dispatched_count": len(tasks),
                    "failed": [failed_server_id],
                    "unreachable_count": 1,
                    "not_attempted": not_attempted,
                    "skipped": skipped,
                    "skipped_count": len(skipped),
                    "login": account.login,
                    "department_id": account.department_id,
                    "server_id": failed_server_id,
                },
            )
            # Mass-режим + хотя бы один успешный dispatch — не отбиваем 503,
            # а отдаём структурированный response с `partial_failure=True`.
            # Auto-cancel не делаем (риск частичных откатов на боксах, куда
            # task уже долетел и применился). UI получает task_ids для
            # ручной отмены и список not_attempted для retry.
            if mode == "all" and tasks:
                audit_service.emit(
                    "mass_rotation.partial_failure",
                    target_id=account_id, target_type="server_account",
                    status="warning", allowed=True,
                    details={
                        "batch_id": batch_id,
                        "task_kind": "account.rotate_password",
                        "dispatched_count": len(tasks),
                        "failed_count": 1,
                        "not_attempted_count": len(not_attempted),
                        "dispatched_task_ids": [t["task_id"] for t in tasks],
                        "failed_server_id": failed_server_id,
                        "not_attempted_server_ids": not_attempted,
                        "login": account.login,
                        "department_id": account.department_id,
                    },
                )
                # Дописываем failed + not_attempted в skipped, чтобы клиент
                # увидел их в одном списке с decommissioned/idempotent.
                skipped.append({
                    "server_id": failed_server_id,
                    "server_name": name_by_id.get(failed_server_id),
                    "reason": "worker_unreachable",
                })
                for sid in not_attempted:
                    skipped.append({
                        "server_id": sid,
                        "server_name": name_by_id.get(sid),
                        "reason": "not_attempted",
                    })
                dispatched_tasks = [AccountRotateTask(**t) for t in tasks]
                failed_entries = [AccountRotateSkipped(**s) for s in skipped]
                return AccountRotateDispatchResponse(
                    batch_id=batch_id,
                    mode=mode,
                    status="partial",
                    dispatched=dispatched_tasks,
                    failed=failed_entries,
                    tasks=dispatched_tasks,
                    skipped=failed_entries,
                    partial_failure=True,
                    next_action="manual_cancel_dispatched",
                )
            raise
        tasks.append({
            "server_id": server.id,
            "server_name": _server_name(server),
            "task_id": task_id,
        })

    # Агрегированный итог: эмитим всегда, даже при частичных пропусках, чтобы
    # частичное применение массовой ротации было видно в SIEM (а не только
    # per-server failure упавшего сервера).
    audit_service.emit(
        audit_action, target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "batch_id": batch_id,
            "mode": mode,
            "task_kind": "account.rotate_password",
            "task_ids": [t["task_id"] for t in tasks],
            "server_ids": [t["server_id"] for t in tasks],
            "dispatched": len(tasks),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "idempotent_hits": idempotent_hits,
            "idempotent_hits_count": len(idempotent_hits),
            "login": account.login,
            "department_id": account.department_id,
        },
    )
    dispatched_tasks = [AccountRotateTask(**t) for t in tasks]
    failed_entries = [AccountRotateSkipped(**s) for s in skipped]
    return AccountRotateDispatchResponse(
        batch_id=batch_id,
        mode=mode,
        status="queued",
        dispatched=dispatched_tasks,
        failed=failed_entries,
        tasks=dispatched_tasks,
        skipped=failed_entries,
        partial_failure=False,
        next_action=None,
    )


# ── /server-accounts/{id}/provision|update_on_host|deprovision ──────────────


@router_accounts.post(
    "/provision",
    response_model=AccountProvisionDispatchResponse,
    summary="Завести OS-пользователя на сервере через worker (useradd)",
    status_code=202,
    description=(
        "Публикует задачу `account.provision`. Worker заходит на сервер по SSH "
        "и выполняет `useradd` (логин из аккаунта, пароль — расшифрованный общий "
        "секрет, sudo/группы/shell/home — из аккаунта), затем POST'ит статус в "
        "`/internal/.../provision_status` (server_service ставит "
        "`present_on_server=True`).\n\n"
        "Параметр `server_id` (query) обязателен и должен быть среди привязанных "
        "к аккаунту серверов. Идемпотентно: если пользователь на боксе уже есть "
        "— worker не падает.\n\n"
        "Триггер гейтится `(server_account, *, provision)` — отдельным "
        "действием ролевой матрицы (или прямым per-account грантом)."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли/гранта с `provision` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept, либо server_id не привязан."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT / ACCOUNT_HAS_NO_PASSWORD (discovered-аккаунт без сохранённого пароля и без `force_password=true`)."},
        503: {"description": "Worker недоступен — WORKER_REDIS_UNAVAILABLE (Redis-stash для provision-кред недоступен) или WORKER_UNREACHABLE / WORKER_REDIS_NOT_CONFIGURED (dispatch в taskiq)."},
    },
)
async def account_provision_dispatch(
    account_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
    server_id: str = Query(
        ..., description="Привязанный сервер, на котором завести пользователя.",
    ),
    force_password: bool = Query(
        default=False,
        description=(
            "Для discovered-аккаунтов без сохранённого пароля: "
            "сгенерировать новый и принудительно перезаписать его на боксе "
            "через chpasswd. Без этого флага discovered-аккаунт без пароля "
            "отбивается 409 (`ACCOUNT_HAS_NO_PASSWORD`), чтобы случайно не "
            "затереть руками выставленный пароль. Managed-аккаунт и "
            "discovered с уже сохранённым паролем игнорируют этот флаг."
        ),
    ),
) -> AccountProvisionDispatchResponse:
    """Ставит `account.provision` (useradd) в очередь worker'а.

    Доступ: `(server_account, *, provision)`. Связано:
    `server_worker/src/tasks/users.py::account_provision`.
    """
    result = await _dispatch_account_provision(
        db=db, identity=identity, request=request,
        account_id=account_id, server_id=server_id,
        audit_action="server_account.provision",
        task_kind="account.provision",
        force_password=force_password,
    )
    return AccountProvisionDispatchResponse(**result)


@router_accounts.post(
    "/update_on_host",
    response_model=AccountProvisionDispatchResponse,
    summary="Синхронизировать атрибуты OS-пользователя на сервере (usermod)",
    status_code=202,
    description=(
        "Публикует задачу `account.update_on_host`. Worker выполняет `usermod` "
        "— синхронизирует sudo/группы/shell аккаунта на боксе, затем POST'ит "
        "статус в `/internal/.../provision_status` (`present_on_server=True`).\n\n"
        "`server_id` (query) обязателен и должен быть привязан. Идемпотентно. "
        "Пароль этой операцией не меняется — для пароля есть `/rotate`.\n\n"
        "Триггер гейтится `(server_account, *, update)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `update` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept, либо server_id не привязан."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_update_on_host_dispatch(
    account_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
    server_id: str = Query(
        ..., description="Привязанный сервер, на котором синхронизировать атрибуты.",
    ),
) -> AccountProvisionDispatchResponse:
    """Ставит `account.update_on_host` (usermod) в очередь worker'а.

    Доступ: `(server_account, *, update)`. Связано:
    `server_worker/src/tasks/users.py::account_update_on_host`.
    """
    result = await _dispatch_account_on_host(
        db=db, identity=identity, request=request,
        account_id=account_id, server_id=server_id,
        action=Action.UPDATE,
        acl_action=Action.UPDATE,
        audit_action="server_account.update_on_host",
        task_kind="account.update_on_host",
        operation="update",
        # usermod без `-d`: смена home существующего юзера через PATCH не
        # делается, worker'ский modify_user `home_dir` не принимает.
        include_home_dir=False,
    )
    return AccountProvisionDispatchResponse(**result)


@router_accounts.post(
    "/deprovision",
    response_model=AccountProvisionDispatchResponse,
    summary="Удалить OS-пользователя с сервера через worker (userdel)",
    status_code=202,
    description=(
        "Публикует задачу `account.deprovision`. Worker выполняет `userdel` "
        "(опционально `--remove` для удаления home), затем POST'ит статус в "
        "`/internal/.../provision_status` (`present_on_server=False`).\n\n"
        "`server_id` (query) обязателен и должен быть привязан. `remove_home` "
        "(query, дефолт false) — удалять ли home-директорию. Идемпотентно: "
        "если пользователя на боксе уже нет — worker не падает. Связку "
        "аккаунт ↔ сервер снимаем сразу после постановки userdel'а — deprovision "
        "и есть полная отвязка от сервера.\n\n"
        "Триггер гейтится `(server_account, *, deprovision)` — отдельным "
        "действием ролевой матрицы (или прямым per-account грантом)."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли/гранта с `deprovision` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept, либо server_id не привязан."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_deprovision_dispatch(
    account_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
    server_id: str = Query(
        ..., description="Привязанный сервер, с которого удалить пользователя.",
    ),
    remove_home: bool = Query(
        default=False,
        description="Удалять ли home-директорию (`userdel --remove`).",
    ),
) -> AccountProvisionDispatchResponse:
    """Ставит `account.deprovision` (userdel) в очередь worker'а.

    Доступ: `(server_account, *, deprovision)`. Связано:
    `server_worker/src/tasks/users.py::account_deprovision`.
    """
    result = await _dispatch_account_on_host(
        db=db, identity=identity, request=request,
        account_id=account_id, server_id=server_id,
        action=Action.DEPROVISION,
        acl_action=Action.DEPROVISION,
        audit_action="server_account.deprovision",
        task_kind="account.deprovision",
        operation="deprovision",
        extra_payload={"remove_home": remove_home},
        # userdel home не использует — флаг отдельный (`remove_home`).
        include_home_dir=False,
    )
    # Снос OS-учётки поставлен в очередь — сразу снимаем связку аккаунт ↔ сервер
    # в БД. До этого deprovision лишь ставил present_on_server=False, связка
    # висела вечно; теперь deprovision и есть полная отвязка от сервера.
    link = await account_repo.get_link(db, account_id, server_id)
    if link is not None:
        await db.delete(link)
        await db.commit()
    return AccountProvisionDispatchResponse(**result)


# ── /server-accounts/{id}/vms/{vm_id}/provision|update_on_host|deprovision ──


@router_accounts.post(
    "/vms/{vm_id}/provision",
    response_model=AccountVmProvisionDispatchResponse,
    status_code=202,
    summary="Завести учётку в госте ВМ через worker (useradd)",
    description=(
        "Публикует задачу `vm.account_provision`. Worker заходит на hub-сервер "
        "ВМ под управляющей учёткой, оттуда в гостя и выполняет `useradd` (+ "
        "chpasswd общим паролем учётки, + authorized_keys публичным ключом, + "
        "группы/sudo). Учётка — общий пул: тот же `server_account`, что и на "
        "серверах. ВМ обязана быть привязана к учётке (`POST /server-accounts/"
        "{id}/vms`). Триггер гейтится `(server_account, *, provision)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли/гранта с `provision` либо чужой department."},
        404: {"description": "Учётка/ВМ не найдена, чужой dept, либо ВМ не привязана."},
        409: {"description": "HUB_UNAVAILABLE / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_vm_provision_dispatch(
    account_id: str,
    vm_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountVmProvisionDispatchResponse:
    """Ставит `vm.account_provision` (useradd в госте) в очередь worker'а.

    Доступ: `(server_account, *, provision)`. Связано:
    `server_worker/src/tasks/vms_accounts.py::vm_account_provision`.
    """
    account, vm, hub = await _resolve_account_and_vm(
        db=db, identity=identity, account_id=account_id, vm_id=vm_id,
        action=Action.PROVISION, acl_action=Action.PROVISION,
        audit_action="server_account.vm_provision", operation="provision",
    )
    result = await _dispatch_vm_account_task(
        db=db, identity=identity, request=request,
        account=account, vm=vm, hub=hub,
        task_kind="vm.account_provision",
        audit_action="server_account.vm_provision",
        operation="provision", include_attrs=True,
    )
    return AccountVmProvisionDispatchResponse(**result)


@router_accounts.post(
    "/vms/{vm_id}/update_on_host",
    response_model=AccountVmProvisionDispatchResponse,
    status_code=202,
    summary="Синхронизировать атрибуты учётки в госте ВМ (usermod)",
    description=(
        "Публикует задачу `vm.account_update_on_host`. Worker в госте ВМ "
        "выполняет `usermod` — синхронизирует sudo/группы аккаунта. Пароль этой "
        "операцией не меняется. ВМ обязана быть привязана. Триггер гейтится "
        "`(server_account, *, update)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли с `update` либо чужой department."},
        404: {"description": "Учётка/ВМ не найдена, чужой dept, либо ВМ не привязана."},
        409: {"description": "HUB_UNAVAILABLE / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_vm_update_on_host_dispatch(
    account_id: str,
    vm_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountVmProvisionDispatchResponse:
    """Ставит `vm.account_update_on_host` (usermod в госте) в очередь worker'а.

    Доступ: `(server_account, *, update)`. Связано:
    `server_worker/src/tasks/vms_accounts.py::vm_account_update_on_host`.
    """
    account, vm, hub = await _resolve_account_and_vm(
        db=db, identity=identity, account_id=account_id, vm_id=vm_id,
        action=Action.UPDATE, acl_action=Action.UPDATE,
        audit_action="server_account.vm_update_on_host", operation="update",
    )
    result = await _dispatch_vm_account_task(
        db=db, identity=identity, request=request,
        account=account, vm=vm, hub=hub,
        task_kind="vm.account_update_on_host",
        audit_action="server_account.vm_update_on_host",
        operation="update", include_attrs=True,
    )
    return AccountVmProvisionDispatchResponse(**result)


@router_accounts.post(
    "/vms/{vm_id}/deprovision",
    response_model=AccountVmProvisionDispatchResponse,
    status_code=202,
    summary="Удалить учётку из гостя ВМ через worker (userdel)",
    description=(
        "Публикует задачу `vm.account_deprovision`. Worker в госте ВМ выполняет "
        "`userdel` (опционально `--remove` для home). ВМ обязана быть привязана. "
        "Связку учётка ↔ ВМ снимаем сразу после постановки задачи — deprovision "
        "и есть полная отвязка от ВМ. Триггер гейтится `(server_account, *, "
        "deprovision)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли/гранта с `deprovision` либо чужой department."},
        404: {"description": "Учётка/ВМ не найдена, чужой dept, либо ВМ не привязана."},
        409: {"description": "HUB_UNAVAILABLE / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_vm_deprovision_dispatch(
    account_id: str,
    vm_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
    remove_home: bool = Query(
        default=False,
        description="Удалять ли home-директорию в госте (`userdel --remove`).",
    ),
) -> AccountVmProvisionDispatchResponse:
    """Ставит `vm.account_deprovision` (userdel в госте) в очередь worker'а.

    Доступ: `(server_account, *, deprovision)`. Связано:
    `server_worker/src/tasks/vms_accounts.py::vm_account_deprovision`.
    """
    account, vm, hub = await _resolve_account_and_vm(
        db=db, identity=identity, account_id=account_id, vm_id=vm_id,
        action=Action.DEPROVISION, acl_action=Action.DEPROVISION,
        audit_action="server_account.vm_deprovision", operation="deprovision",
    )
    result = await _dispatch_vm_account_task(
        db=db, identity=identity, request=request,
        account=account, vm=vm, hub=hub,
        task_kind="vm.account_deprovision",
        audit_action="server_account.vm_deprovision",
        operation="deprovision", include_attrs=False, remove_home=remove_home,
    )
    # Снос учётки поставлен — сразу снимаем связку учётка ↔ ВМ в БД (симметрия
    # с серверным deprovision: он и есть полная отвязка от ВМ).
    link = await account_repo.get_vm_link(db, account_id, vm_id)
    if link is not None:
        await db.delete(link)
        await db.commit()
    return AccountVmProvisionDispatchResponse(**result)


# ── /ipmi-controllers/{id}/rotate ───────────────────────────────────────────


@router_ipmi.post(
    "/rotate",
    response_model=ServerTaskDispatchResponse,
    summary="Ротация IPMI-пароля через worker (Redfish apply + storage)",
    status_code=202,
    description=(
        "Публикует задачу `ipmi.rotate_password`. Worker заходит на BMC старым "
        "паролем, генерит новый, применяет его (Redfish PATCH / ipmitool), "
        "под новым паролем делает read-only verify (доказательство, что BMC "
        "принял пароль), и только после verify шлёт ciphertext в "
        "`/internal/.../credentials_rotated` (storage round-trip). Verify не "
        "прошёл — storage не коммитится, задача FAILED, оператор разбирается "
        "по audit'у."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `rotate_credentials` либо чужой department."},
        404: {"description": "IPMI-контроллер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        429: {"description": "RATE_LIMIT_EXCEEDED — rotate-rate-limit пробит (ключ — IP + controller_id)."},
        503: {"description": "Worker недоступен."},
    },
)
@endpoint_limiter.limit(
    get_settings().ipmi_rotate_per_server_rate_limit,
    key_func=per_account_key,
)
async def ipmi_rotate_password_dispatch(
    request: Request,
    controller_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Ставит `ipmi.rotate_password` в очередь worker'а.

    Доступ: `(ipmi_controller, *, rotate_credentials)`. Cross-dept controller
    скрыт за 404.

    Связано: `server_worker/src/tasks/passwords.py::ipmi_rotate_password`
    (verify-then-submit: BMC apply → read-only verify → storage round-trip).
    """
    audit_action = "ipmi_controller.rotate_dispatch"

    with emit_denied_on_authz_error(
        audit_action,
        target_id=controller_id,
        target_type="ipmi_controller",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.ROTATE_CREDENTIALS,
        )

    controller = await ipmi_repo.get_by_id(db, controller_id)
    if controller is None:
        audit_service.emit(
            audit_action, target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="IPMI controller not found",
        )
    # `load_visible_server` даёт consolidated dept-isolation, ловим 404 и
    # перерапиваем в NO_IPMI_CONTROLLER, иначе SERVER_NOT_FOUND
    # error_code раскрыл бы, что controller_id ссылается на чужой сервер.
    try:
        server = await server_svc.load_visible_server(db, identity, controller.server_id)
    except NotFoundError as exc:
        audit_service.emit(
            audit_action, target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="IPMI controller not found",
        ) from exc

    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "decommissioned",
                "server_id": server.id,
                "department_id": server.department_id,
            },
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned, IPMI rotation not allowed",
        )

    # BMC apply идёт на живом хосте — занятый чужим владельцем или ACS-
    # снимком сервер трогать нельзя, тот же гейт, что у остальных dispatch'ей.
    reservation.ensure_not_reserved_for(identity, server, action=audit_action)
    reservation.ensure_not_acs_locked(identity, server)

    idempotency_key = read_idempotency_key(request)
    # Помечаем строку controller'а pending_apply=True до dispatch'а: worker
    # будет генерить новый пароль и применять его на BMC, а callback
    # `record_ipmi_credentials_rotated` сохранит ciphertext и снимет флаг.
    # Между dispatch'ем и callback'ом БД-ciphertext (старый) и BMC-пароль
    # (свежий) могут разойтись — retry увидит pending_apply=True и
    # сможет отличить «свежий dispatch» от «callback просто запоздал».
    pending_before = bool(controller.credentials_pending_apply)
    controller.credentials_pending_apply = True
    await db.flush()
    payload = {
        "server_id": server.id,
        "controller_id": controller_id,
        "target_department_id": server.department_id,
        # force_replace для retry-сценария: было had_password_before AND NOT
        # pending_apply, теперь — был ли подтверждён предыдущий dispatch.
        # Worker не использует это поле на ipmi.rotate_password (он сам
        # генерит и применяет), но кладём для симметрии с account-провижном
        # и SIEM-аудитом: оператор видит «retry с force-overwrite».
        "force_replace": pending_before,
    }
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind="ipmi.rotate_password",
            target_server_id=server.id,
            target_resource_id=controller_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
    except ConflictError:
        # ConflictError из dispatch_task_with_hit бьёт в двух сценариях:
        # IDEMPOTENCY_KEY_REUSE_CONFLICT (клиент перепутал ключи) и
        # TASK_IDEMPOTENT_CONFLICT (UNIQUE-race + повторный SELECT не нашёл
        # строку). В обоих случаях ни одна task не поставлена в очередь —
        # наша мутация `pending_apply=True` врала бы оператору про
        # «незавершённую ротацию», которой нет. Откатываем.
        await db.rollback()
        audit_service.emit(
            audit_action, target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "idempotent_conflict",
                "task_kind": "ipmi.rotate_password",
                "server_id": server.id,
                "department_id": server.department_id,
            },
        )
        raise
    except ServiceUnavailableError:
        # Воркер недоступен — нашу мутацию pending_apply'а откатываем,
        # никакого dispatch'а не случилось, БД не должна оставаться в
        # «pending» состоянии без задачи в очереди.
        await db.rollback()
        audit_service.emit(
            audit_action, target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "worker_unreachable",
                "task_kind": "ipmi.rotate_password",
                "server_id": server.id,
                "department_id": server.department_id,
            },
        )
        raise
    await db.commit()
    audit_service.emit(
        audit_action, target_id=controller_id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": "ipmi.rotate_password",
            "server_id": server.id,
            "department_id": server.department_id,
            "idempotent_hit": idempotent_hit,
        },
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")
