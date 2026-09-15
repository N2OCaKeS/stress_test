"""Обзор пула с нуля (§F плана 2026-09-11) — реальные агрегаты очереди и стендов.

Заменяет прежний синтетический `FleetDashboard` фронтенда (удалён вместе с
placeholder-метриками). Чтение открыто любому аутентифицированному актору —
как у `test_run`/`test_stand`, без отдельной записи в матрице прав.
"""

from pydantic import AwareDatetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import DomainValidationError
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.pool_overview import PoolOverviewResponse, PoolOverviewTestRun
from src.services import pool_overview as svc

router = APIRouter()


@router.get(
    "/pool-overview",
    response_model=PoolOverviewResponse,
    summary="Обзор пула: очередь, исходы, статусы стендов",
    description=(
        "Три контекста: `all` (весь отдел), `run` (одна кампания — требует "
        "`test_run_id`), `standalone` (самостоятельные запуски, опционально "
        "период `created_from`/`created_until`). Пустой пул/очередь — нули, "
        "не 500/null."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "TEST_RUN_NOT_FOUND — кампания не найдена или чужого отдела."},
        422: {"description": "INVALID_TIME_RANGE / TEST_RUN_ID_REQUIRED / TEST_RUN_ID_NOT_ALLOWED."},
    },
)
async def get_pool_overview(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    context: str = Query(default="all", pattern="^(all|run|standalone)$"),
    test_run_id: str | None = Query(default=None),
    created_from: AwareDatetime | None = Query(default=None, description="Только для context=standalone."),
    created_until: AwareDatetime | None = Query(default=None, description="Только для context=standalone."),
) -> PoolOverviewResponse:
    """Get обзора пула. Любой аутентифицированный актор своего отдела."""
    if created_from and created_until and created_from >= created_until:
        raise DomainValidationError(
            error_code="INVALID_TIME_RANGE", message="Начало периода должно быть раньше конца",
        )
    overview = await svc.get_overview(
        db,
        department_id=identity.department_id or "",
        context=context,  # type: ignore[arg-type]
        test_run_id=test_run_id,
        created_from=created_from, created_until=created_until,
    )
    return PoolOverviewResponse(
        context=overview.context,
        test_run_id=overview.test_run_id,
        test_run=(
            PoolOverviewTestRun.model_validate(overview.test_run) if overview.test_run else None
        ),
        remaining=overview.remaining,
        running=overview.running,
        succeeded=overview.succeeded,
        failed=overview.failed,
        stands=[
            {
                "stand_id": stand.stand_id,
                "server_id": stand.server_id,
                "status": stand.status,
                "busy_state": stand.busy_state,
                "busy_service_name": stand.busy_service_name,
                "ping_reachable": stand.ping_reachable,
                "ping_checked_at": stand.ping_checked_at,
            }
            for stand in overview.stands
        ],
        stand_status_counts=overview.stand_status_counts,
        generated_at=overview.generated_at,
    )
