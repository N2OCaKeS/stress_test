"""Inventory: lightweight probe + bulk-submit от worker'а.

* `POST /servers/{id}/inventory/probe` — лёгкая проверка железа (faster чем
  full sync). Dispatch `inventory.probe` worker'у.
* Canonical full-sync — `POST /servers/{id}/inventory/sync` в
  `endpoints/worker_dispatch.py` (та же permission, но другой task_kind).

Bulk-submit endpoint (write-direction от worker'а) приходит через
`/internal/servers/{id}/inventory` (см. `endpoints/internal.py`).
"""

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
from src.schemas.server import ServerTaskDispatchResponse
from src.services import audit_service, permissions, worker_client
from src.services import server as server_svc

router = APIRouter(prefix="/servers/{server_id}/inventory")


@router.post(
    "/probe",
    response_model=ServerTaskDispatchResponse,
    status_code=202,
    summary="Лёгкая проверка железа через worker (быстрее full inventory.sync)",
    description=(
        "Публикует задачу `inventory.probe` в taskiq-broker. Worker делает "
        "ping/uname/быстрый smoke-check без полного сбора пакетов и дисков. "
        "Тот же permission, что у full-sync (`inventory_trigger`), но другой "
        "task_kind — клиенты могут запрашивать частые light-проверки без "
        "нагрузки full collection. Полная инвентаризация — `POST /inventory/sync` "
        "(см. worker_dispatch.py). Доступ: `(server, *, inventory_trigger)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли с `inventory_trigger`."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def trigger_probe(
    server_id: str,
    identity: CurrentIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Dispatch lightweight inventory probe. Доступ: `(server, *, inventory_trigger)`.

    Логика идентична worker_dispatch.inventory_sync, но task_kind = `inventory.probe`
    + другой audit-action (`server.inventory_probe`). BMC не нужен — probe
    идёт по SSH либо ICMP, поэтому `SERVER_NO_IPMI` не проверяется.
    """
    audit_action = "server.inventory_probe"
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        reason = (
            "not_found_or_cross_dept" if isinstance(exc, NotFoundError)
            else "no_view_permission"
        )
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": reason},
        )
        raise
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.INVENTORY_TRIGGER,
        )
    except AuthorizationError:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": server.department_id},
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
            message="Server is decommissioned and cannot be probed",
        )
    idempotency_key = request.headers.get("Idempotency-Key") or None
    payload = {"server_id": server_id, "target_department_id": server.department_id}
    try:
        task_id = await worker_client.dispatch_task(
            task_kind="inventory.probe",
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
                "task_kind": "inventory.probe",
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
                "task_kind": "inventory.probe",
                "department_id": server.department_id,
            },
        )
        raise
    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": "inventory.probe",
            "department_id": server.department_id,
        },
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")


# ── /servers/{id}/users/inventory — OS-user inventory via SSH ───────────────


users_router = APIRouter(prefix="/servers/{server_id}/users")


@users_router.post(
    "/inventory",
    response_model=ServerTaskDispatchResponse,
    status_code=202,
    summary="Запустить инвентаризацию OS-пользователей через SSH (202, worker)",
    description=(
        "Публикует задачу `users.inventory` в taskiq-broker. Worker заходит "
        "на сервер по SSH (через привязанный аккаунт, если в payload передан "
        "`account_id`, иначе дефолтный `root`), читает `getent passwd` / группы "
        "/ sudoers, фильтрует системных по `UID_MIN` из `/etc/login.defs` и "
        "POST'ит список обратно в `/internal/servers/{id}/users/inventory`. "
        "server_service reconcile'ит его с `server_accounts`. "
        "Право — тот же `inventory_trigger`, что и у hardware-инвентаризации. "
        "Доступ: `(server, *, inventory_trigger)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        403: {"description": "Нет роли с `inventory_trigger`."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def trigger_users_inventory(
    server_id: str,
    identity: CurrentIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Dispatch инвентаризации OS-пользователей. Доступ: `(server, *, inventory_trigger)`.

    Тот же паттерн, что у `inventory.sync`: visibility → role → decommissioned →
    SSH-сбор без BMC. task_kind = `users.inventory`, audit-action =
    `server_account.users_inventory`.
    """
    audit_action = "server_account.users_inventory"
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        reason = (
            "not_found_or_cross_dept" if isinstance(exc, NotFoundError)
            else "no_view_permission"
        )
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": reason},
        )
        raise
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.INVENTORY_TRIGGER,
        )
    except AuthorizationError:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": server.department_id},
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
            message="Server is decommissioned and cannot be inventoried",
        )
    idempotency_key = request.headers.get("Idempotency-Key") or None
    payload = {"server_id": server_id, "target_department_id": server.department_id}
    try:
        task_id = await worker_client.dispatch_task(
            task_kind="users.inventory",
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
                "task_kind": "users.inventory",
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
                "task_kind": "users.inventory",
                "department_id": server.department_id,
            },
        )
        raise
    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": "users.inventory",
            "department_id": server.department_id,
        },
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")
