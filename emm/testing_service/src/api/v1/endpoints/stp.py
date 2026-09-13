"""СТП: каталог тест-кейсов, генерация Zephyr test-run'ов, прогоны, ячейки (§2.5, §6 плана миграции).

Тест-кейсы — чтение открыто любому аутентифицированному актору, запись —
матрица `(stp_test_case, *, ...)`. `/stp/generate` — админский вызов,
матрица `(stp_test_run, *, create)`. Ячейки — ручной override под
`(stp_cell, *, update)`, событийное обновление идёт мимо HTTP (см.
`services/queue.py` → `services/stp_status.py`).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.stp import (
    StpCellManualUpdate,
    StpCellResponse,
    StpGenerateRequest,
    StpGenerateResponse,
    StpTestCaseCreate,
    StpTestCaseResponse,
    StpTestCaseUpdate,
    StpTestRunResponse,
)
from src.services import stp as stp_svc
from src.services import stp_status
from src.services import stp_test_case as stp_test_case_svc

router = APIRouter(prefix="/stp")


# ── Тест-кейсы ────────────────────────────────────────────────────────────────


@router.get(
    "/test-cases",
    response_model=PaginatedResponse[StpTestCaseResponse],
    summary="Каталог тест-кейсов СТП",
    description="Зеркало Zephyr Scale test-case. Доступен любому аутентифицированному актору.",
)
async def list_stp_test_cases(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    department_id: str | None = Query(default=None),
) -> PaginatedResponse[StpTestCaseResponse]:
    items, total = await stp_test_case_svc.list_stp_test_cases(
        db, limit=limit, offset=offset, department_id=department_id,
    )
    return PaginatedResponse[StpTestCaseResponse](
        items=[StpTestCaseResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.post(
    "/test-cases",
    response_model=StpTestCaseResponse,
    status_code=201,
    summary="Завести тест-кейс СТП",
    description="UNIQUE(code) — повтор → 409. `zephyr_id` опционален, заполняется оператором позже.",
    responses={403: {"description": "Нет роли с `create`."}, 409: {"description": "Дубль по code."}},
)
async def create_stp_test_case(
    body: StpTestCaseCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpTestCaseResponse:
    obj = await stp_test_case_svc.create_stp_test_case(db, identity, body)
    return StpTestCaseResponse.model_validate(obj)


@router.get(
    "/test-cases/{case_id}",
    response_model=StpTestCaseResponse,
    summary="Карточка тест-кейса СТП",
)
async def get_stp_test_case(
    case_id: str, identity: AuthenticatedIdentity, db: AsyncSession = Depends(get_db),
) -> StpTestCaseResponse:
    obj = await stp_test_case_svc.get_stp_test_case(db, case_id)
    return StpTestCaseResponse.model_validate(obj)


@router.patch(
    "/test-cases/{case_id}",
    response_model=StpTestCaseResponse,
    summary="Обновить тест-кейс СТП",
    responses={403: {"description": "Нет `update`."}, 404: {"description": "Не найден."}},
)
async def update_stp_test_case(
    case_id: str,
    body: StpTestCaseUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpTestCaseResponse:
    obj = await stp_test_case_svc.update_stp_test_case(db, identity, case_id, body)
    return StpTestCaseResponse.model_validate(obj)


@router.delete(
    "/test-cases/{case_id}",
    response_model=OkResponse,
    summary="Удалить тест-кейс СТП",
    responses={403: {"description": "Нет `delete`."}, 404: {"description": "Не найден."}},
)
async def delete_stp_test_case(
    case_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await stp_test_case_svc.delete_stp_test_case(db, identity, case_id)
    return OkResponse()


# ── Генерация + прогоны ────────────────────────────────────────────────────────


@router.post(
    "/generate",
    response_model=StpGenerateResponse,
    summary="Сгенерировать СТП-прогоны в Zephyr для отдела/РЦ/режима/ядра",
    description=(
        "Админский вызов (не автоматический вебхук). Changelog-фильтр (§1/§7), "
        "группировка по pinned-стенду, резолв Jira-кред отдела, создание "
        "Zephyr test-run на стенд. Провал одного стенда не рушит остальные — "
        "см. `errors` в ответе."
    ),
    responses={403: {"description": "Нет роли с `create`."}},
)
async def generate_stp(
    body: StpGenerateRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpGenerateResponse:
    runs, errors = await stp_svc.generate_stp_runs(
        db, identity,
        os_version_id=body.os_version_id, mode=body.mode, kernel=body.kernel,
        final=body.final, department_id=body.department_id or identity.department_id,
    )
    return StpGenerateResponse(
        test_runs=[StpTestRunResponse.model_validate(r) for r in runs],
        errors=errors,
    )


@router.get(
    "/test-runs",
    response_model=PaginatedResponse[StpTestRunResponse],
    summary="Список СТП-прогонов (Zephyr test-run'ов)",
)
async def list_stp_test_runs(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    stand_id: str | None = Query(default=None),
    os_version_id: str | None = Query(default=None),
) -> PaginatedResponse[StpTestRunResponse]:
    items, total = await stp_svc.list_stp_test_runs(
        db, limit=limit, offset=offset, stand_id=stand_id, os_version_id=os_version_id,
    )
    return PaginatedResponse[StpTestRunResponse](
        items=[StpTestRunResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.get(
    "/test-runs/{run_id}",
    response_model=StpTestRunResponse,
    summary="Карточка СТП-прогона",
    responses={404: {"description": "Не найден."}},
)
async def get_stp_test_run(
    run_id: str, identity: AuthenticatedIdentity, db: AsyncSession = Depends(get_db),
) -> StpTestRunResponse:
    run, _cells = await stp_svc.get_stp_test_run(db, run_id)
    return StpTestRunResponse.model_validate(run)


@router.get(
    "/test-runs/{run_id}/cells",
    response_model=list[StpCellResponse],
    summary="Ячейки СТП-прогона",
    responses={404: {"description": "Прогон не найден."}},
)
async def list_stp_test_run_cells(
    run_id: str, identity: AuthenticatedIdentity, db: AsyncSession = Depends(get_db),
) -> list[StpCellResponse]:
    _run, cells = await stp_svc.get_stp_test_run(db, run_id)
    return [StpCellResponse.model_validate(c) for c in cells]


# ── Ячейки: ручной override ─────────────────────────────────────────────────────


@router.patch(
    "/cells/{cell_id}",
    response_model=StpCellResponse,
    summary="Ручной override статуса ячейки СТП",
    description="Не трогает Zephyr — легаси-паттерн: ручная правка локальна.",
    responses={403: {"description": "Нет `update`."}, 404: {"description": "Ячейка не найдена."}},
)
async def override_stp_cell(
    cell_id: str,
    body: StpCellManualUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpCellResponse:
    obj = await stp_status.manual_override(db, identity, cell_id, body.status)
    return StpCellResponse.model_validate(obj)
