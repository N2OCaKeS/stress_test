"""Worker-dispatch endpoints для read-only/maintenance task'ов.

Дополняет `endpoints/ipmi.py` (power.on/off/reboot) и user-facing
`endpoints/server_accounts.py` (`/rotate_password` — локальная ротация без
SSH-apply). Дисптачи через `worker_client`:

* ``POST /servers/{id}/power/status``     → `power.status`
  (live BMC-probe, требует IPMI-row).
* ``POST /servers/{id}/inventory/sync``   → `inventory.sync`
* ``POST /server-accounts/{id}/rotate``   → `account.rotate_password`
  (через worker: generate → SSH → submit-обратно; user-facing локальный
  `/rotate_password` в `endpoints/server_accounts.py` пароль только меняет
  в БД, без apply'я на сервер).
* ``POST /ipmi-controllers/{id}/rotate``  → `ipmi.rotate_password`
  (worker сейчас raise'ит NotImplementedError до того, как тронет iDRAC —
  storage round-trip ещё не существует. Endpoint всё равно поднимает таску,
  worker mark_failed + audit failure через `_runner` — это полный
  defense-in-depth контракт «вызов фиксируется до того, как handler
  откажет», см. server_worker/src/tasks/passwords.py SAFETY GUARD).

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

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.repositories import ipmi_controller as ipmi_repo
from src.repositories import server_account as account_repo
from src.schemas.server import ServerPowerStatusDispatchResponse
from src.services import audit_service, permissions, worker_client
from src.services import server as server_svc
from src.services.audit_helpers import emit_denied_on_authz_error

logger = logging.getLogger(__name__)

router_servers = APIRouter(prefix="/servers/{server_id}")
router_accounts = APIRouter(prefix="/server-accounts/{account_id}")
router_ipmi = APIRouter(prefix="/ipmi-controllers/{controller_id}")


# ── helpers ─────────────────────────────────────────────────────────────────


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

      1. ``server_svc.get_server`` (VIEW + dept-isolation) — 404 cross-dept
         ДО role-check, чтобы caller из чужого dep_b не определил по 403/404
         существование сервера из dep_a.
      2. ``require_action(action)`` — для конкретной операции.
      3. ``SERVER_DECOMMISSIONED`` — списанные сервера не принимают ни одной
         worker-операции.
      4. ``SERVER_NO_IPMI`` — для task'ов, которые ходят в BMC (power.status).
         Для inventory.sync — пропускаем: handler идёт по SSH.
      5. ``worker_client.dispatch_task`` + audit-emit на каждой ветке.

    ``extra_payload`` мерджится поверх стандартного ``{server_id,
    target_department_id}`` — нужен для редких task-kind'ов с собственными
    полями (например, `inventory.sync` опционально берёт ``account_id``).
    """
    # 1. Visibility + dept isolation.
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        reason = "not_found_or_cross_dept" if isinstance(exc, NotFoundError) else "no_view_permission"
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": reason},
        )
        raise

    # 2. Role-check.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"department_id": server.department_id},
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, action)

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
            raise ConflictError(
                error_code="SERVER_NO_IPMI",
                message="Server has no IPMI controller configured (BMC endpoint/credentials missing)",
            )

    # 5. Dispatch + audit.
    idempotency_key = request.headers.get("Idempotency-Key") or None
    payload: dict = {"server_id": server_id, "target_department_id": server.department_id}
    if extra_payload:
        payload.update(extra_payload)
    try:
        task_id = await worker_client.dispatch_task(
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
        },
    )
    return {"task_id": task_id, "status": "queued"}


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
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404)."},
        409: {"description": "SERVER_DECOMMISSIONED / SERVER_NO_IPMI / TASK_IDEMPOTENT_CONFLICT."},
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
    409 SERVER_DECOMMISSIONED, 409 SERVER_NO_IPMI, 409 TASK_IDEMPOTENT_CONFLICT,
    503 WORKER_UNREACHABLE.

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
        "сервер по SSH (используя любой аккаунт сервера, если в payload "
        "передан `account_id`, иначе дефолтный `root`), снимает OS/kernel/"
        "packages/disks и сохраняет в `task.result`. Постинг facts обратно "
        "в server_service — пока TODO в worker'е (см. inventory.py)."
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


# ── /server-accounts/{id}/rotate — admin-initiated worker rotation ──────────


@router_accounts.post(
    "/rotate",
    summary="Ротация пароля аккаунта через worker (SSH apply + storage)",
    status_code=202,
    description=(
        "Публикует задачу `account.rotate_password`. Worker сгенерит новый "
        "пароль, применит через SSH (`chpasswd`) и POST'нет обратно в "
        "server_service internal endpoint, который зашифрует и сохранит. "
        "В отличие от `/server-accounts/{id}/rotate_password` (user-facing, "
        "меняет только запись в БД без apply'я на сервер) — этот dispatch "
        "обновляет пароль end-to-end. Plaintext клиенту не возвращается."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли с `rotate_password` либо чужой department."},
        404: {"description": "Аккаунт не найден / чужой dept (скрыто за 404)."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def account_rotate_password_dispatch(
    account_id: str,
    identity: CurrentIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Ставит `account.rotate_password` в очередь worker'а.

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
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.ROTATE_PASSWORD,
        )

    # Visibility-check: подгружаем аккаунт и его сервер, dept должен совпасть
    # с caller'ом. Любая ambiguity (нет аккаунта, нет сервера, чужой dept)
    # → одинаковая 404 ACCOUNT_NOT_FOUND, иначе по разнице ответов утечёт
    # cross-dept enumeration.
    account = await account_repo.get_by_id(db, account_id)
    if account is None:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
        )
    # Используем load_visible_server для consolidated dept-isolation (404 на
    # cross-dept), но перерапиваем 404 в ACCOUNT_NOT_FOUND — иначе по
    # error_code (SERVER_NOT_FOUND vs ACCOUNT_NOT_FOUND) утечёт, что
    # запрашиваемый account_id ссылается на чужой сервер.
    try:
        server = await server_svc.load_visible_server(db, identity, account.server_id)
    except NotFoundError as exc:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
        ) from exc

    if server.status == ServerStatus.DECOMMISSIONED:
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

    idempotency_key = request.headers.get("Idempotency-Key") or None
    payload = {
        "server_id": server.id,
        "account_id": account_id,
        "target_department_id": server.department_id,
    }
    try:
        task_id = await worker_client.dispatch_task(
            task_kind="account.rotate_password",
            target_server_id=server.id,
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
                "task_kind": "account.rotate_password",
                "server_id": server.id,
                "department_id": server.department_id,
            },
        )
        raise
    except ServiceUnavailableError:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "worker_unreachable",
                "task_kind": "account.rotate_password",
                "server_id": server.id,
                "department_id": server.department_id,
            },
        )
        raise
    audit_service.emit(
        audit_action, target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": "account.rotate_password",
            "server_id": server.id,
            "login": account.login,
            "department_id": server.department_id,
        },
    )
    return {"task_id": task_id, "status": "queued"}


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
        503: {"description": "Worker недоступен."},
    },
)
async def ipmi_rotate_password_dispatch(
    controller_id: str,
    identity: CurrentIdentity,
    request: Request,
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
    ):
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.ROTATE_CREDENTIALS,
        )

    controller = await ipmi_repo.get_by_id(db, controller_id)
    if controller is None:
        audit_service.emit(
            audit_action, target_id=controller_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="IPMI_CONTROLLER_NOT_FOUND",
            message="IPMI controller not found",
        )
    # `load_visible_server` даёт consolidated dept-isolation, ловим 404 и
    # перерапиваем в IPMI_CONTROLLER_NOT_FOUND, иначе SERVER_NOT_FOUND
    # error_code раскрыл бы, что controller_id ссылается на чужой сервер.
    try:
        server = await server_svc.load_visible_server(db, identity, controller.server_id)
    except NotFoundError as exc:
        audit_service.emit(
            audit_action, target_id=controller_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="IPMI_CONTROLLER_NOT_FOUND",
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

    idempotency_key = request.headers.get("Idempotency-Key") or None
    payload = {
        "server_id": server.id,
        "controller_id": controller_id,
        "target_department_id": server.department_id,
    }
    try:
        task_id = await worker_client.dispatch_task(
            task_kind="ipmi.rotate_password",
            target_server_id=server.id,
            target_resource_id=controller_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
    except ConflictError:
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
    audit_service.emit(
        audit_action, target_id=controller_id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": "ipmi.rotate_password",
            "server_id": server.id,
            "department_id": server.department_id,
        },
    )
    return {"task_id": task_id, "status": "queued"}
