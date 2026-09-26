"""`/statistics` — платформенные настройки + индикатор фонового пересчёта (§2.7, §9.3 плана миграции).

`GET /statistics/settings` / `PUT /statistics/settings` — platform singleton,
тот же паттерн, что `/department-test-settings`: чтение открыто любому
аутентифицированному актору, запись — под матрицей `(statistics_settings, *,
update)`.

`GET /statistics/status` — текущее/последнее состояние фонового пересчёта
(idle/running/succeeded/failed) для индикатора на левой панели UI. Открыт
так же, как настройки.

`POST /statistics/recalculate` — ручной триггер пересчёта (debug-страница,
модалка на страницах прогонов и СТП). Кампании (`test_run`)
пересчитывают статистику автоматически на терминальном статусе — см.
`services/queue.py`. Без `category`/`categories` — полный пересчёт, с ними —
выбранные семейства тестов (легаси-кнопки `allta_app/allta_front.py:713-880`).

`/statistics/categories` — справочник семейств (D18): чтение открыто
любому аутентифицированному актору, запись (`POST`/`PATCH`/`DELETE`) — под
тем же `(statistics_settings, *, update)`. Сид — восемь семейств легаси.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.statistics_category import (
    StatisticsCategoriesResponse,
    StatisticsCategoryCreate,
    StatisticsCategoryResponse,
    StatisticsCategoryUpdate,
)
from src.schemas.statistics_recalc import (
    StatisticsRecalcStatusResponse,
    StatisticsRecalcTriggerRequest,
)
from src.schemas.statistics_settings import StatisticsSettingsResponse, StatisticsSettingsUpdate
from src.services import statistics_category as category_svc
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


@router.get(
    "/categories",
    response_model=StatisticsCategoriesResponse,
    summary="Справочник семейств тестов для пересчёта статистики",
    description=(
        "Порядок — `sort_order` (сид — как в легаси-меню). Ключ отсюда передаётся в "
        "`POST /statistics/recalculate` полем `category`/`categories`; без них пересчёт "
        "полный. По умолчанию только включённые; `include_disabled=true` — весь "
        "справочник (страница настроек). Доступен любому аутентифицированному актору."
    ),
    responses={401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."}},
)
async def get_statistics_categories(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    include_disabled: bool = Query(default=False),
) -> StatisticsCategoriesResponse:
    rows = await category_svc.list_categories(db, include_disabled=include_disabled)
    return StatisticsCategoriesResponse(
        items=[StatisticsCategoryResponse.model_validate(row) for row in rows],
    )


@router.post(
    "/categories",
    response_model=StatisticsCategoryResponse,
    status_code=201,
    summary="Добавить семейство тестов в справочник статистики",
    description="Право: `(statistics_settings, *, update)`. UNIQUE(key) → 409.",
    responses={
        403: {"description": "Нет роли с `update`."},
        409: {"description": "STATISTICS_CATEGORY_DUPLICATE — такой `key` уже есть."},
    },
)
async def post_statistics_category(
    payload: StatisticsCategoryCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StatisticsCategoryResponse:
    obj = await category_svc.create_category(db, identity, payload)
    return StatisticsCategoryResponse.model_validate(obj)


@router.patch(
    "/categories/{category_id}",
    response_model=StatisticsCategoryResponse,
    summary="Изменить семейство тестов в справочнике статистики",
    description=(
        "Частичное обновление, `key` не меняется. Явный `null` в "
        "`comparison_list`/`comparison_kernel_list` очищает поле. "
        "Право: `(statistics_settings, *, update)`."
    ),
    responses={
        403: {"description": "Нет роли с `update`."},
        404: {"description": "STATISTICS_CATEGORY_NOT_FOUND."},
    },
)
async def patch_statistics_category(
    category_id: str,
    payload: StatisticsCategoryUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StatisticsCategoryResponse:
    obj = await category_svc.update_category(db, identity, category_id, payload)
    return StatisticsCategoryResponse.model_validate(obj)


@router.delete(
    "/categories/{category_id}",
    response_model=OkResponse,
    summary="Удалить семейство тестов из справочника статистики",
    description="Hard-delete. Право: `(statistics_settings, *, update)`.",
    responses={
        403: {"description": "Нет роли с `update`."},
        404: {"description": "STATISTICS_CATEGORY_NOT_FOUND."},
    },
)
async def delete_statistics_category(
    category_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await category_svc.delete_category(db, identity, category_id)
    return OkResponse()


@router.post(
    "/recalculate",
    response_model=StatisticsRecalcStatusResponse,
    status_code=202,
    summary="Запустить пересчёт статистики вручную",
    description=(
        "Ставит фоновый пересчёт (не блокирует ответ и не блокирует очередь "
        "тестов). `department_id` не передан — берётся отдел вызывающего. "
        "`category`/`categories` не переданы — полный пересчёт, иначе выбранные "
        "семейства тестов последовательно одной фоновой задачей "
        "(ключи — `GET /statistics/categories`). "
        "Право: `(statistics_settings, *, update)`."
    ),
    responses={
        403: {"description": "Нет роли с `update`."},
        422: {
            "description": (
                "DEPARTMENT_ID_REQUIRED — у вызывающего нет своего отдела и он не "
                "передан явно; STATISTICS_CATEGORY_UNKNOWN — неизвестный ключ семейства; "
                "STATISTICS_CATEGORY_DISABLED — семейство выключено в справочнике."
            ),
        },
    },
)
async def post_statistics_recalculate(
    payload: StatisticsRecalcTriggerRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StatisticsRecalcStatusResponse:
    await recalc_svc.trigger_manual(
        db, identity, payload.department_id, payload.category, payload.categories,
    )
    data = await recalc_svc.get_status(db)
    return StatisticsRecalcStatusResponse(**data)
