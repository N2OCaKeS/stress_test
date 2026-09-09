"""Прогоны — fleet-wide кампании (§2.4, §6.1 плана миграции).

Создание — под матрицей прав `(test_run, *, create)`. Чтение (список/карточка)
доступно любому аутентифицированному актору, как у `test_definition`/
`test_stand` — сама постановка тестов в очередь уже гейтится их собственной
матрицей на уровне queue.py (роль `admin` требуется на create кампании, а не
на каждый вложенный queue_item).
"""

from fastapi import APIRouter, Depends, Query

from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity, CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import PaginatedResponse
from src.schemas.test_run import (
    TestRunCreate,
    TestRunCreateResponse,
    TestRunDetailResponse,
    TestRunQueueItemResponse,
    TestRunResponse,
)
from src.services import test_run as svc

router = APIRouter(prefix="/test-runs")


@router.post(
    "",
    response_model=TestRunCreateResponse,
    status_code=201,
    summary="Запустить кампанию на пуле стендов",
    description=(
        "Один РЦ+ядро+режим, поставленный в очередь сразу на весь явно "
        "выбранный пул стендов (по одному тесту, закреплённому за каждым "
        "стендом через `pinned_stand_id`). Стенд без закреплённых тестов не "
        "рушит кампанию — попадает в `stands_without_tests`. Провал "
        "постановки одного теста одного стенда — в `enqueue_errors`, "
        "остальные стенды кампании стартуют независимо."
    ),
    responses={
        201: {"description": "Кампания создана (возможно, частично — см. stands_without_tests/enqueue_errors)."},
        403: {"description": "Нет роли с `create` на `test_run`."},
        422: {"description": "У вызывающего нет department_id, либо тело запроса невалидно."},
    },
)
async def create_test_run(
    body: TestRunCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestRunCreateResponse:
    """Create кампании. Доступ: `(test_run, *, create)`."""
    run, stands_without_tests, enqueue_errors = await svc.create_test_run(
        db, identity,
        os_version_id=body.os_version_id, mode=body.mode, kernel=body.kernel,
        test_run_stands=body.test_run_stands, final=body.final,
    )
    response = TestRunCreateResponse.model_validate(run)
    response.stands_without_tests = stands_without_tests
    response.enqueue_errors = enqueue_errors
    return response


@router.get(
    "",
    response_model=PaginatedResponse[TestRunResponse],
    summary="Список кампаний",
    description="Кампании с опциональными фильтрами по отделу/статусу/финальности. Доступен любому аутентифицированному актору.",
    responses={401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."}},
)
async def list_test_runs(
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    department_id: str | None = Query(default=None, description="Фильтр по отделу-инициатору."),
    status: str | None = Query(default=None, description="Фильтр по агрегатному статусу кампании."),
    final: bool | None = Query(default=None, description="Фильтр по флагу финального/официального прогона."),
) -> PaginatedResponse[TestRunResponse]:
    """List кампаний. Любой аутентифицированный актор."""
    items, total = await svc.list_test_runs(
        db, limit=limit, offset=offset, department_id=department_id, status=status, final=final,
    )
    return PaginatedResponse[TestRunResponse](
        items=[TestRunResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{run_id}",
    response_model=TestRunDetailResponse,
    summary="Детали кампании",
    description="Карточка кампании + все дочерние queue_items с их состояниями (агрегатный обзор).",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Кампания не найдена."},
    },
)
async def get_test_run(
    run_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestRunDetailResponse:
    """Get кампании по id. Любой аутентифицированный актор."""
    run, items = await svc.get_test_run(db, run_id)
    response = TestRunDetailResponse.model_validate(run)
    response.queue_items = [
        TestRunQueueItemResponse(
            queue_item_id=item.id,
            stand_id=item.stand_id,
            test_id=item.test_id,
            state=item.state,
            is_retry=item.is_retry,
            started_at=item.started_at,
            finished_at=item.finished_at,
            error=item.error,
        )
        for item in items
    ]
    return response
