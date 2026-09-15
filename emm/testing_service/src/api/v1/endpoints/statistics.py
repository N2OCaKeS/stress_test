"""`/statistics` — платформенные настройки + индикатор фонового пересчёта (§2.7, §9.3 плана миграции).

`GET /statistics/settings` / `PUT /statistics/settings` — platform singleton,
тот же паттерн, что `/department-test-settings`: чтение открыто любому
аутентифицированному актору, запись — под матрицей `(statistics_settings, *,
update)`.

`GET /statistics/status` — текущее/последнее состояние фонового пересчёта
(idle/running/succeeded/failed) для индикатора на левой панели UI. Открыт
так же, как настройки.

`POST /statistics/recalculate` — ручной триггер пересчёта для одиночных
(standalone) тестов. Кампании (`test_run`) пересчитывают статистику
автоматически на терминальном статусе — см. `services/queue.py`.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.statistics_recalc import (
    StatisticsRecalcStatusResponse,
    StatisticsRecalcTriggerRequest,
)
from src.schemas.statistics_settings import StatisticsSettingsResponse, StatisticsSettingsUpdate
from src.services import statistics_recalc as recalc_svc
from src.services import statistics_settings as settings_svc

router = APIRouter(prefix="/statistics")


@router.get(
    "/settings",
    response_model=StatisticsSettingsResponse,
    summary="Текущие настройки внешнего сервиса статистики",
    description="Доступен любому аутентифицированному актору. Дефолты, если строка ещё не создана.",
    responses={401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."}},
)
async def get_statistics_settings(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> StatisticsSettingsResponse:
    data = await settings_svc.get_effective(db)
    return StatisticsSettingsResponse(**data)


@router.put(
    "/settings",
    response_model=StatisticsSettingsResponse,
    summary="Изменить настройки внешнего сервиса статистики",
    description="Частичное обновление. Право: `(statistics_settings, *, update)`.",
    responses={403: {"description": "Нет роли с `update`."}},
)
async def put_statistics_settings(
    payload: StatisticsSettingsUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StatisticsSettingsResponse:
    data = await settings_svc.update_settings(db, identity, payload)
    return StatisticsSettingsResponse(**data)


@router.get(
    "/status",
    response_model=StatisticsRecalcStatusResponse,
    summary="Статус фонового пересчёта статистики",
    description=(
        "Индикатор для левой панели: idle/running/succeeded/failed, время "
        "последнего запуска/завершения. Доступен любому аутентифицированному актору."
    ),
    responses={401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."}},
)
async def get_statistics_status(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> StatisticsRecalcStatusResponse:
    data = await recalc_svc.get_status(db)
    return StatisticsRecalcStatusResponse(**data)


@router.post(
    "/recalculate",
    response_model=StatisticsRecalcStatusResponse,
    status_code=202,
    summary="Запустить пересчёт статистики вручную",
    description=(
        "Ставит фоновый пересчёт (не блокирует ответ и не блокирует очередь "
        "тестов). `department_id` не передан — берётся отдел вызывающего. "
        "Право: `(statistics_settings, *, update)`."
    ),
    responses={
        403: {"description": "Нет роли с `update`."},
        422: {"description": "DEPARTMENT_ID_REQUIRED — у вызывающего нет своего отдела и он не передан явно."},
    },
)
async def post_statistics_recalculate(
    payload: StatisticsRecalcTriggerRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StatisticsRecalcStatusResponse:
    await recalc_svc.trigger_manual(db, identity, payload.department_id)
    data = await recalc_svc.get_status(db)
    return StatisticsRecalcStatusResponse(**data)
