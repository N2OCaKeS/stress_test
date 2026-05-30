"""Cancel-эндпоинт worker-task'и.

`POST /api/server/v1/tasks/{task_id}/cancel` — единственная точка
управления task-row'ой со стороны server_service. Row живёт в
`dev_server_worker.tasks`; server_service ходит туда через cross-DB engine
из `worker_client` (тот же, что dispatch_task инсёртит при постановке).

Поведение:

* permission: `(task, cancel)` — отдельная пара из матрицы. По-дефолту
  выдаётся `admin` сидингом миграции; operator/reader без явного гранта
  получают 403.
* visibility: если у task есть `target_server_id`, проверяем dept-isolation
  на этом сервере (cross-dept → 404 ``TASK_NOT_FOUND``, как обычно).
  Task без target_server_id (heartbeat и подобные) доступен только тем,
  у кого есть `(task, cancel)` — таких тасок оператор обычно не отменяет.
* cancellable-precondition: pending/running. Terminal (succeeded/failed/
  cancelled) → 409 ``TASK_NOT_CANCELLABLE``.
* graceful: row помечается status=cancelled + cancelled_by/cancelled_at/
  cancel_reason. Running-task'у не убивает принудительно — worker сам
  доживёт текущий stage и не стартанёт следующий (CAS на mark_running
  в `_runner` отобьёт повторный pickup).
* audit: `task.cancelled` (WARNING по дефолту) с details.task_kind /
  target_server_id / previous_status.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.task import TaskCancelRequest, TaskCancelResponse
from src.services import audit_service, permissions, worker_client
from src.services import server as server_svc
from src.services.audit_helpers import emit_denied_on_authz_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks")


@router.post(
    "/{task_id}/cancel",
    response_model=TaskCancelResponse,
    summary="Отменить pending/running worker-task'у",
    description=(
        "Помечает row в `dev_server_worker.tasks` как `cancelled`. "
        "Pending-task пропускается перед запуском через CAS на mark_running. "
        "Running-task graceful: текущий stage доживает, следующий не стартует. "
        "Force-kill нет. Доступ: `(task, cancel)` — по-дефолту только `admin`."
    ),
    responses={
        403: {"description": "Нет роли с `cancel` на task."},
        404: {"description": "Task не найдена (нет row либо cross-dept по target_server_id)."},
        409: {"description": "Task в терминальном статусе (succeeded/failed/cancelled) и не подлежит отмене."},
    },
)
async def cancel_task_endpoint(
    identity: CurrentIdentity,
    task_id: str = Path(..., min_length=1, max_length=64),
    body: TaskCancelRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> TaskCancelResponse:
    audit_action = "task.cancelled"
    reason = body.reason if body is not None else None

    # 1. Role-check (permission → visibility, как в остальных endpoint'ах).
    with emit_denied_on_authz_error(
        audit_action,
        target_id=task_id,
        target_type="task",
    ):
        await permissions.require_action(db, identity, EntityType.TASK, Action.CANCEL)

    # 2. Подгрузить статус и метаданные target'а из worker-БД.
    try:
        meta = await worker_client._fetch_task_status_and_meta(task_id)
    except ServiceUnavailableError:
        # WORKER_DB_NOT_CONFIGURED и подобные — не отменяем, отдаём 503
        # без денайд-аудита (это не отказ доступа caller'у, это infra).
        raise
    if meta is None:
        audit_service.emit(
            audit_action, target_id=task_id, target_type="task",
            status="denied", allowed=False,
            details={"reason": "task_not_found"},
        )
        raise NotFoundError(
            error_code="TASK_NOT_FOUND",
            message="Task not found",
        )

    target_server_id = meta.get("target_server_id")
    task_kind = meta.get("task_kind")

    # 3. Dept-isolation по target_server_id, если есть. Cross-dept маскируем
    #    под 404 — стандартный enumeration-guard.
    if target_server_id is not None:
        try:
            await server_svc.load_visible_server(db, identity, target_server_id)
        except (NotFoundError, AuthorizationError):
            audit_service.emit(
                audit_action, target_id=task_id, target_type="task",
                status="denied", allowed=False,
                details={
                    "reason": "task_not_found_or_cross_dept",
                    "target_server_id": target_server_id,
                    "task_kind": task_kind,
                },
            )
            raise NotFoundError(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
            )

    # 4. Atomic UPDATE через cross-DB engine.
    result = await worker_client.cancel_task(
        task_id_value=task_id,
        cancelled_by=identity.user_id,
        cancel_reason=reason,
    )

    if not result["found"]:
        # Гонка: row исчез между _fetch_task_status_and_meta и cancel_task.
        # Маловероятно (cleanup task'а удаляет только terminal succeeded/
        # failed), но проще честно вернуть 404 чем притворяться.
        audit_service.emit(
            audit_action, target_id=task_id, target_type="task",
            status="denied", allowed=False,
            details={"reason": "task_not_found"},
        )
        raise NotFoundError(
            error_code="TASK_NOT_FOUND",
            message="Task not found",
        )

    previous_status = result.get("previous_status")
    if not result["cancelled"]:
        audit_service.emit(
            audit_action, target_id=task_id, target_type="task",
            status="failure", allowed=True,
            details={
                "reason": "not_cancellable",
                "previous_status": previous_status,
                "task_kind": task_kind,
                "target_server_id": target_server_id,
            },
        )
        raise ConflictError(
            error_code="TASK_NOT_CANCELLABLE",
            message=(
                f"Task is in terminal status '{previous_status}' and cannot be cancelled"
            ),
            details={"previous_status": previous_status},
        )

    cancelled_at = datetime.now(timezone.utc)
    audit_service.emit(
        audit_action, target_id=task_id, target_type="task",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "previous_status": previous_status,
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "cancel_reason": reason,
        },
    )
    return TaskCancelResponse(
        task_id=task_id,
        status="cancelled",
        previous_status=previous_status,
        cancelled_at=cancelled_at,
        cancelled_by=identity.user_id,
        cancel_reason=reason,
    )
