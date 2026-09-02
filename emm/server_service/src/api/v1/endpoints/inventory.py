"""OS-user инвентаризация через SSH (worker dispatch).

* `POST /servers/{id}/users/inventory` — getent passwd/groups + sudoers,
  reconcile с `server_accounts`. task_kind = `users.inventory`.

Hardware-инвентаризация (`inventory.sync`) живёт в
`endpoints/worker_dispatch.py`. Bulk-submit с результатами от worker'а —
`/internal/servers/{id}/inventory` (см. `endpoints/internal.py`).
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.endpoints._dispatch import dispatch_server_ssh_task
from src.api.v1.endpoints.worker_dispatch import require_server_prepared
from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.server import ServerTaskDispatchResponse
from src.services import audit_service, permissions
from src.services import server as server_svc
from src.services.audit_helpers import emit_denied_on_authz_error


# ── /servers/{id}/users/inventory — OS-user inventory via SSH ───────────────


users_router = APIRouter(prefix="/servers/{server_id}/users")


@users_router.post(
    "/inventory",
    response_model=ServerTaskDispatchResponse,
    status_code=202,
    summary="Запустить инвентаризацию OS-пользователей через SSH (202, worker)",
    description=(
        "Публикует задачу `users.inventory` в taskiq-broker. Worker заходит "
        "на сервер по SSH под управляющим пользователем (`management_user`) — "
        "сервер обязан быть подготовлен (`is_managed`, через prepare), иначе "
        "409 PREPARE_REQUIRED. Читает `getent passwd` / группы / sudoers, "
        "фильтрует системных по `UID_MIN` из `/etc/login.defs` и POST'ит список "
        "обратно в `/internal/servers/{id}/users/inventory`. server_service "
        "reconcile'ит его с `server_accounts`. Право — тот же "
        "`inventory_trigger`, что и у hardware-инвентаризации. Доступ: "
        "`(server, *, inventory_trigger)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `inventory_trigger`."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / PREPARE_REQUIRED (сервер не prepared) / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен."},
    },
)
async def trigger_users_inventory(
    server_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Dispatch инвентаризации OS-пользователей. Доступ: `(server, *, inventory_trigger)`.

    Тот же паттерн, что у `_dispatch_for_server` (см. `endpoints/worker_dispatch.py`):
    permission → visibility → decommissioned → prepare-gate → SSH-сбор без BMC.
    Permission ДО visibility — чтобы 403 не превращался в existence-oracle по `server_id`.
    task_kind = `users.inventory`, audit-action = `server.users_inventory_triggered`
    (target=server — namespace ожидает server-target для server-scoped действий;
    SIEM-фильтр по `server_account.*` относится к самим аккаунтам, dispatch же
    кикается со стороны сервера).
    """
    audit_action = "server.users_inventory_triggered"
    # 1. Role-check — раньше visibility, чтобы caller без права не отличал
    # «нет сервера» от «нет роли» по статус-коду.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.INVENTORY_TRIGGER,
        )
    # 2. Visibility + dept isolation.
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        # NotFoundError — visibility-404 (cross-dept / нет row): failure+allowed=True.
        # AuthorizationError — нет VIEW при наличии других прав: denied+allowed=False.
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
            message="Server is decommissioned and cannot be inventoried",
        )
    # Prepare-gate: SSH-сбор идёт под управляющим ключом (после prepare).
    # Неподготовленный сервер → 409 PREPARE_REQUIRED.
    require_server_prepared(server, audit_action=audit_action)

    # Dispatch + audit — общая обвязка в `_dispatch.dispatch_server_ssh_task`.
    # На managed-сервере worker заходит по ключу — аккаунта в payload нет.
    task_id, _ = await dispatch_server_ssh_task(
        db=db, identity=identity, request=request,
        server=server,
        task_kind="users.inventory",
        audit_action=audit_action,
        resolved_account_id=None,
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")
