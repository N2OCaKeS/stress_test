"""OS-user инвентаризация через SSH (worker dispatch).

* `POST /servers/{id}/users/inventory` — getent passwd/groups + sudoers,
  reconcile с `server_accounts`. task_kind = `users.inventory`.

Hardware-инвентаризация (`inventory.sync`) живёт в
`endpoints/worker_dispatch.py`. Bulk-submit с результатами от worker'а —
`/internal/servers/{id}/inventory` (см. `endpoints/internal.py`).
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.endpoints._dispatch import dispatch_server_ssh_task
from src.api.v1.endpoints.worker_dispatch import resolve_inventory_account_id
from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
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
        "на сервер по SSH — под управляющим пользователем (`management_user`) "
        "если сервер `is_managed`, иначе под дефолтным аккаунтом сессии — "
        "читает `getent passwd` / группы / sudoers, фильтрует системных по "
        "`UID_MIN` из `/etc/login.defs` и POST'ит список обратно в "
        "`/internal/servers/{id}/users/inventory`. server_service reconcile'ит "
        "его с `server_accounts`. Выбор аккаунта: на управляемом сервере "
        "(`is_managed`) worker заходит по ключу под `management_user`, аккаунт "
        "не нужен. На неуправляемом — нужен пароль аккаунта (self-сессия): "
        "передай `account_id` явно либо server_service возьмёт дефолтный "
        "привязанный аккаунт (первый с сохранённым паролем). Привязок нет — "
        "422 ACCOUNT_REQUIRED. Право — тот же `inventory_trigger`, что и у "
        "hardware-инвентаризации. Доступ: `(server, *, inventory_trigger)`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "IDEMPOTENCY_KEY_TOO_LONG — заголовок длиннее лимита."},
        403: {"description": "Нет роли с `inventory_trigger`."},
        404: {"description": "Сервер не найден / чужой dept."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        422: {"description": "ACCOUNT_REQUIRED (неуправляемый сервер без привязанных аккаунтов) / ACCOUNT_NOT_LINKED (account_id не привязан к серверу)."},
        503: {"description": "Worker недоступен."},
    },
)
async def trigger_users_inventory(
    server_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
    account_id: str | None = Query(
        default=None,
        description=(
            "Аккаунт сервера, под которым worker зайдёт по SSH (self-сессия "
            "по паролю). Для управляемого сервера игнорируется (вход по "
            "ключу). Не передан — server_service берёт дефолтный привязанный "
            "аккаунт; привязок нет — 422 ACCOUNT_REQUIRED."
        ),
    ),
) -> ServerTaskDispatchResponse:
    """Dispatch инвентаризации OS-пользователей. Доступ: `(server, *, inventory_trigger)`.

    Тот же паттерн, что у `_dispatch_for_server` (см. `endpoints/worker_dispatch.py`):
    permission → visibility → decommissioned → SSH-сбор без BMC. Permission ДО
    visibility — чтобы 403 не превращался в existence-oracle по `server_id`.
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
    # Account-резолв для self-сессии (неуправляемый сервер). Без `account_id`
    # worker фоллбэчился на root без пароля → SSH_AUTH_FAILED. Managed → None
    # (вход по ключу). Нет аккаунта / не привязан → 422.
    try:
        resolved_account_id = await resolve_inventory_account_id(
            db, server, account_id,
        )
    except DomainValidationError as exc:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": exc.error_code.lower(),
                "task_kind": "users.inventory",
                "department_id": server.department_id,
            },
        )
        raise

    # Dispatch + audit — общая обвязка в `_dispatch.dispatch_server_ssh_task`
    # (базовый SSH-payload + dispatch с target_resource_id + ConflictError/
    # ServiceUnavailableError-audit + commit + success-audit). Резолвнутый
    # аккаунт (явный или дефолтный) кладётся и в payload, и в колонку task-row.
    task_id, _ = await dispatch_server_ssh_task(
        db=db, identity=identity, request=request,
        server=server,
        task_kind="users.inventory",
        audit_action=audit_action,
        resolved_account_id=resolved_account_id,
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")
