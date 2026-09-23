"""Обзор пула с нуля (§F плана 2026-09-11, доработка 2026-09-23) — реальные
агрегаты очереди и стендов.

Заменяет прежний синтетический `FleetDashboard` фронтенда (удалён вместе с
placeholder-метриками). Чтение открыто любому аутентифицированному актору —
как у `test_run`/`test_stand`, без отдельной записи в матрице прав.

Больше не принимает `context`/`test_run_id` от вызывающего — сервис сам
решает режим агрегации succeeded/failed (`active_run` при незавершённой
кампании отдела, `rolling_24h` иначе), см. `services/pool_overview.py`.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

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
        "`remaining`/`running` — по всему отделу, всегда. `succeeded`/`failed` "
        "зависят от режима, который сервис выбирает сам: `active_run` — если "
        "у отдела есть незавершённая кампания (пока не закончится, сколько бы "
        "дней ни шла), `rolling_24h` — статистика за последние сутки, если "
        "активных кампаний нет. Пустой пул/очередь — нули, не 500/null."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
    },
)
async def get_pool_overview(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> PoolOverviewResponse:
    """Get обзора пула. Любой аутентифицированный актор своего отдела."""
    overview = await svc.get_overview(db, department_id=identity.department_id or "")
    return PoolOverviewResponse(
        mode=overview.mode,
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
