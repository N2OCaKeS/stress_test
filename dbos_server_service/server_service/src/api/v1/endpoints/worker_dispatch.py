"""Worker-dispatch endpoints для maintenance/inventory/provision task'ов.

Дополняет `endpoints/ipmi.py` (power.on/off/reboot) и user-facing
`endpoints/server_accounts.py` (`/rotate_password` — локальная ротация без
SSH-apply). Дисптачи через `worker_client.dispatch_task`:

* ``POST /servers/{id}/power/status``      → `power.status`
  (live BMC-probe, требует IPMI-row).
* ``POST /servers/{id}/inventory/sync``    → `inventory.sync`
  (full SSH-probe: lscpu/lsblk/os-release).
* ``POST /servers/{id}/users/inventory``   → `users.inventory`
  (getent → reconcile в server_accounts).
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

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import AccountSource, Action, EntityType, ServerStatus
from src.core.limiter import endpoint_limiter, per_account_key
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.dependencies.idempotency import read_idempotency_key
from src.repositories import ipmi_controller as ipmi_repo
from src.repositories import server_account as account_repo
from src.schemas.server import (
    ServerPowerStatusDispatchResponse,
    ServerPrepareRequest,
    ServerPrepareResponse,
)
from src.schemas.server_account import (
    AccountProvisionDispatchResponse,
    AccountRotateDispatchResponse,
    AccountRotateSkipped,
    AccountRotateTask,
)
from src.services import (
    audit_service,
    permissions,
    server_account as account_svc,
    worker_client,
)
from src.services import server as server_svc
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import dispatch_creds_id, prepare_creds_id

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
        # ssh_client._extract_host / _extract_port; без них fallback на
        # server_id (UUID) рвёт DNS-резолв на dev-стендах.
        "host": server.hostname,
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
        await permissions.require_action(db, identity, EntityType.SERVER, action)

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

    # 5. Dispatch + audit.
    idempotency_key = read_idempotency_key(request)
    payload: dict = {
        "server_id": server_id,
        "target_department_id": server.department_id,
        # SSH-эндпоинт: воркеру негде взять hostname/port, отправляем явно.
        # ssh_client._extract_host читает `host`, fallback на server_id (UUID)
        # сломал бы DNS-резолв на dev-стендах.
        "host": server.hostname,
        "ssh_port": server.ssh_port,
        # На подготовленном сервере worker заходит под управляющим пользователем
        # по ключу с sudo; inventory.sync собирает факты под ним вместо self-сессии.
        "is_managed": server.is_managed,
        "management_user": server.management_user,
    }
    if extra_payload:
        payload.update(extra_payload)
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            task_kind=task_kind,
            target_server_id=server_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
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
    return {"task_id": task_id, "status": "queued"}


async def _resolve_account_and_server(
    *,
    db: AsyncSession,
    identity,
    account_id: str,
    server_id: str,
    action: str,
    audit_action: str,
    operation: str,
):
    """Permission + visibility + dept-isolation для пары account+server.

    Возвращает `(account, server)`. На любом провале эмитит failure-audit и
    поднимает исключение. Порядок проверок зеркалит
    `account_rotate_password_dispatch`: permission ДО visibility (иначе
    enumeration), затем dept-isolated lookup аккаунта, проверка что
    `server_id` среди привязанных, decommissioned-gate.
    """
    with emit_denied_on_authz_error(
        audit_action,
        target_id=account_id,
        target_type="server_account",
        extra_details={"server_id": server_id, "operation": operation},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, action,
        )

    account = await account_repo.get_by_id(db, account_id)
    if account is None or account.department_id != identity.department_id:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept", "operation": operation},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
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
    if server.status == ServerStatus.DECOMMISSIONED:
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
    )

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
            task_kind=task_kind,
            target_server_id=server_id_v,
            target_resource_id=account_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
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
    * Plaintext password + ssh_private_key уезжают в Redis-stash под
      `dbos:dispatch_creds:<dcd_id>` (TTL = `dispatch_creds_ttl_seconds`),
      в task-payload едет только ссылка `creds_stash_key`. Симметрия с
      `server.prepare` (bootstrap_creds_key).
    """
    operation = "provision"
    account, server = await _resolve_account_and_server(
        db=db, identity=identity, account_id=account_id, server_id=server_id,
        action=Action.CREATE, audit_action=audit_action, operation=operation,
    )

    # Discovered без сохранённого пароля и без явного force_password —
    # fail-fast 409 ДО любых side-effect'ов: ни stash в Redis, ни savepoint,
    # ни generation. Caller знает что делает: либо ротировать пароль через
    # `/rotate_password`, либо явно передать `?force_password=true`.
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

    idempotency_key = read_idempotency_key(request)
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
    # Idempotent-hit: новой публикации в брокер не было, но мы только что
    # положили свежий stash — оригинальная task видит чужой ciphertext по
    # своему ключу, наш stash висит сиротой до TTL. Чистим сразу.
    if idempotent_hit:
        await worker_client.delete_dispatch_creds(stash_key)
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
                task_kind="account.update_on_host",
                target_server_id=server.id,
                target_resource_id=account.id,
                payload=payload,
                created_by=identity.user_id,
                request_id=request_id,
                idempotency_key=per_server_key,
            )
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
        403: {"description": "Нет роли с `power_status` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404) либо NO_IPMI_CONTROLLER."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def power_status_dispatch(
    server_id: str,
    identity: CurrentIdentity,
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
    summary="Запустить inventory-sync через SSH (202, worker)",
    status_code=202,
    description=(
        "Публикует задачу `inventory.sync` в taskiq-broker. Worker идёт на "
        "сервер по SSH под управляющим пользователем (`management_user`) "
        "если сервер `is_managed`, иначе под дефолтным аккаунтом сессии — "
        "снимает OS/kernel/packages/disks и постит facts обратно через "
        "`submit_inventory_facts` (internal endpoint). Сырые facts остаются "
        "в `task.result` для диагностики; submit-fail уходит в audit как "
        "`server.inventory_sync` failure, но сам task остаётся SUCCEEDED."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли с `inventory_trigger` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def inventory_sync_dispatch(
    server_id: str,
    identity: CurrentIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Ставит `inventory.sync` в очередь worker'а.

    Доступ: `(server, *, inventory_trigger)`.

    Возможные ошибки: 403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND,
    409 SERVER_DECOMMISSIONED, 409 TASK_IDEMPOTENT_CONFLICT, 503 WORKER_UNREACHABLE.

    Связано: `_dispatch_for_server`, `server_worker/src/tasks/inventory.py`.
    """
    # SSH-сбор не нуждается в BMC — `require_ipmi=False`.
    return await _dispatch_for_server(
        db=db, identity=identity, request=request,
        server_id=server_id,
        action=Action.INVENTORY_TRIGGER,
        audit_action="server.inventory_sync",
        task_kind="inventory.sync",
        require_ipmi=False,
    )


# ── /servers/{id}/prepare — bootstrap управления (онбординг) ────────────────


@router_servers.post(
    "/prepare",
    response_model=ServerPrepareResponse,
    status_code=202,
    summary="Бутстрап управления сервером через worker (202)",
    description=(
        "Публикует задачу `server.prepare` в taskiq-broker. В теле — bootstrap-"
        "креды (логин и пароль в base64, симметрия с reveal-картами). "
        "server_service декодирует их и прокидывает воркеру через cross-DB "
        "dispatch-канал; воркер заходит на сервер под ними по SSH, заводит "
        "системного управляющего пользователя DBOS, даёт ему sudo и кладёт "
        "публичный ключ управления — дальше управление по ключу без исходного "
        "пароля.\n\n"
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
        403: {"description": "Нет роли с `update` либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404)."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        422: {"description": "Битый base64 в username_b64 / password_b64."},
        429: {"description": "Per-IP prepare-rate-limit пробит."},
        503: {"description": "Worker недоступен."},
    },
)
@endpoint_limiter.limit(get_settings().server_prepare_rate_limit)
async def server_prepare_dispatch(
    request: Request,
    server_id: str,
    body: ServerPrepareRequest,
    identity: CurrentIdentity,
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
        await permissions.require_action(db, identity, EntityType.SERVER, Action.UPDATE)

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

    idempotency_key = read_idempotency_key(request)

    # Idempotency-replay должен идти ДО `store_prepare_creds`. Иначе любой
    # повторный POST с тем же ключом плодит новые plaintext-stash'и в Redis
    # под orphan-ключами, которые задача никогда не прочтёт — каждый висит
    # PREPARE_CREDS_TTL_SECONDS (900s) до естественного истечения. Caller с
    # валидным `update` за это окно может забить Redis plaintext'ом.
    if idempotency_key is not None:
        existing = await worker_client._get_task_by_idempotency_key(idempotency_key)
        if existing is not None:
            try:
                worker_client._ensure_idempotency_matches(
                    existing=existing,
                    task_kind=task_kind,
                    target_server_id=server_id,
                )
            except ConflictError:
                audit_service.emit(
                    audit_action, target_id=server_id, target_type="server",
                    status="failure", allowed=True,
                    details={
                        "reason": "idempotency_key_reuse_conflict",
                        "task_kind": task_kind,
                        "department_id": server.department_id,
                    },
                )
                raise
            existing_id = existing[0]
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
            return ServerPrepareResponse(task_id=existing_id, status="queued")

    # base64 уже провалидирован схемой; декодируем plaintext для воркера.
    # Креды НЕ кладём в task-payload (иначе plaintext осел бы в worker-БД).
    # Пишем их в Redis под одноразовый ключ с TTL, в payload — только ссылка.
    # Воркер читает креды по ссылке на каждой попытке, TTL чистит их сам.
    creds_key = worker_client.prepare_creds_key(prepare_creds_id())
    try:
        await worker_client.store_prepare_creds(
            creds_key,
            {"bootstrap_login": body.username(), "bootstrap_password": body.password()},
        )
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

    payload: dict = {
        "server_id": server_id,
        "target_department_id": server.department_id,
        "host": server.hostname,
        "ssh_port": server.ssh_port,
        "is_managed": server.is_managed,
        "management_user": server.management_user,
        "bootstrap_creds_key": creds_key,
    }
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            task_kind=task_kind,
            target_server_id=server_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
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
    return ServerPrepareResponse(task_id=task_id, status="queued")


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
        "недоступность воркера (redis down) отбивает весь запрос 503.\n\n"
        "В отличие от `/server-accounts/{id}/rotate_password` (user-facing, "
        "меняет только запись в БД без apply'я) — этот dispatch обновляет "
        "пароль end-to-end. Plaintext клиенту не возвращается."
    ),
    responses={
        202: {"description": "Задача(и) приняты; tasks + skipped в теле ответа."},
        403: {"description": "Нет роли с `rotate_password` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept, либо server_id не привязан."},
        409: {"description": "SERVER_DECOMMISSIONED (точечно либо все списаны) / TASK_IDEMPOTENT_CONFLICT (точечно) / NO_LINKED_SERVERS (массово, аккаунт без привязок)."},
        503: {"description": "Worker недоступен (redis down / не сконфигурён)."},
    },
)
async def account_rotate_password_dispatch(
    account_id: str,
    identity: CurrentIdentity,
    request: Request,
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

    # Permission ДО visibility — иначе ошибки enumerable: caller без роли
    # увидел бы по 403/404 разницу для существующих vs несуществующих
    # account_id'ов. (Симметрия с server_account.rotate_password в
    # `services/server_account.py`.)
    with emit_denied_on_authz_error(
        audit_action,
        target_id=account_id,
        target_type="server_account",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.ROTATE_PASSWORD,
        )

    # Visibility-check: аккаунт виден, если его department совпадает с
    # caller'ом. Любая ambiguity (нет аккаунта, чужой dept) → одинаковая
    # 404 ACCOUNT_NOT_FOUND, иначе по разнице ответов утечёт enumeration.
    account = await account_repo.get_by_id(db, account_id)
    if account is None or account.department_id != identity.department_id:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
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
            skipped.append({"server_id": sid, "reason": "not_found_or_cross_dept"})
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
            skipped.append({"server_id": server.id, "reason": "decommissioned"})
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
                task_kind="account.rotate_password",
                target_server_id=server.id,
                target_resource_id=account_id,
                payload=payload,
                created_by=identity.user_id,
                request_id=request_id,
                idempotency_key=per_server_key,
            )
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
            skipped.append({"server_id": server.id, "reason": "idempotent_conflict"})
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
                    "reason": "worker_unreachable",
                })
                for sid in not_attempted:
                    skipped.append({
                        "server_id": sid, "reason": "not_attempted",
                    })
                return AccountRotateDispatchResponse(
                    mode=mode,
                    status="partial",
                    tasks=[AccountRotateTask(**t) for t in tasks],
                    skipped=[AccountRotateSkipped(**s) for s in skipped],
                    partial_failure=True,
                    next_action="manual_cancel_dispatched",
                )
            raise
        tasks.append({"server_id": server.id, "task_id": task_id})

    # Агрегированный итог: эмитим всегда, даже при частичных пропусках, чтобы
    # частичное применение массовой ротации было видно в SIEM (а не только
    # per-server failure упавшего сервера).
    audit_service.emit(
        audit_action, target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
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
    return AccountRotateDispatchResponse(
        mode=mode,
        status="queued",
        tasks=[AccountRotateTask(**t) for t in tasks],
        skipped=[AccountRotateSkipped(**s) for s in skipped],
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
        "Триггер гейтится `(server_account, *, create)` — создание OS-пользователя "
        "на боксе семантически близко к созданию аккаунта."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли с `create` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept, либо server_id не привязан."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_provision_dispatch(
    account_id: str,
    identity: CurrentIdentity,
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

    Доступ: `(server_account, *, create)`. Связано:
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
        403: {"description": "Нет роли с `update` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept, либо server_id не привязан."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_update_on_host_dispatch(
    account_id: str,
    identity: CurrentIdentity,
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
        "аккаунт ↔ сервер эта операция НЕ снимает (для отвязки — `/servers`).\n\n"
        "Триггер гейтится `(server_account, *, delete)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли с `delete` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept, либо server_id не привязан."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_deprovision_dispatch(
    account_id: str,
    identity: CurrentIdentity,
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

    Доступ: `(server_account, *, delete)`. Связано:
    `server_worker/src/tasks/users.py::account_deprovision`.
    """
    result = await _dispatch_account_on_host(
        db=db, identity=identity, request=request,
        account_id=account_id, server_id=server_id,
        action=Action.DELETE,
        audit_action="server_account.deprovision",
        task_kind="account.deprovision",
        operation="deprovision",
        extra_payload={"remove_home": remove_home},
        # userdel home не использует — флаг отдельный (`remove_home`).
        include_home_dir=False,
    )
    return AccountProvisionDispatchResponse(**result)


# ── /ipmi-controllers/{id}/rotate ───────────────────────────────────────────


@router_ipmi.post(
    "/rotate",
    summary="Ротация IPMI-пароля через worker (Redfish apply + storage)",
    status_code=202,
    description=(
        "Публикует задачу `ipmi.rotate_password`. **Внимание:** worker-handler "
        "сейчас raise'ит `NotImplementedError` ДО любого вызова в iDRAC — "
        "storage round-trip ещё не построен, и без него ротация привела бы "
        "к смене пароля на BMC без сохранения нового ciphertext (out-of-band "
        "доступ был бы потерян навсегда). Endpoint всё равно поднимает таску, "
        "worker `_runner` корректно mark_failed + audit failure. Включение — "
        "после появления storage endpoint'а."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли с `rotate_credentials` либо чужой department."},
        404: {"description": "IPMI-контроллер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        429: {"description": "Rotate-rate-limit пробит (ключ — IP + controller_id)."},
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
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Ставит `ipmi.rotate_password` в очередь worker'а.

    Доступ: `(ipmi_controller, *, rotate_credentials)`. Cross-dept controller
    скрыт за 404.

    Связано: `server_worker/src/tasks/passwords.py::ipmi_rotate_password`
    (DISABLED — см. SAFETY GUARD).
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
            task_kind="ipmi.rotate_password",
            target_server_id=server.id,
            target_resource_id=controller_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
    except ConflictError:
        # Idempotent-конфликт: задача уже стоит на этом ресурсе, наш
        # pending_apply-флаг подождёт того же callback'а. БД-обновление
        # коммитим, чтобы flag не откатился к False — иначе следующий
        # ручной dispatch не понял бы race-состояние.
        await db.commit()
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
    return {"task_id": task_id, "status": "queued"}
