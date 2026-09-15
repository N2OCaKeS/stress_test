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
from src.schemas.run_summary import RunSummaryCommentResponse
from src.schemas.test_run import (
    TestRunEntryResponse,
    TestRunCreate,
    TestRunCreateResponse,
    TestRunDetailResponse,
    TestRunPreviewResponse,
    TestRunQueueItemResponse,
    TestRunResponse,
)
from src.services import log_availability
from src.services import run_summary as run_summary_svc
from src.services import test_run as svc
from src.services.test_run_status import latest_attempts, result_states
from src.repositories import test_run_entry as entry_repo
from collections import Counter

router = APIRouter(prefix="/test-runs")


@router.post(
    "",
    response_model=TestRunCreateResponse,
    status_code=201,
    summary="Запустить кампанию на пуле стендов",
    description=(
        "Один РЦ и режим, все доступные ядра ОС и все тесты выбранного пула "
        "стендов, закреплённые через `pinned_stand_id`. Явный `kernel` "
        "сохраняет совместимость с запуском на одном ядре. Стенд без тестов не "
        "рушит кампанию — попадает в `stands_without_tests`. Провал "
        "постановки одного теста одного стенда — в `enqueue_errors`, "
        "остальные стенды кампании стартуют независимо."
    ),
    responses={
        201: {"description": "Кампания создана (возможно, частично — см. stands_without_tests/enqueue_errors)."},
        403: {"description": "Нет роли с `create` на `test_run`."},
        409: {"description": "`request_id` уже использован с другими параметрами (REQUEST_ID_CONFLICT)."},
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
        request_id=body.request_id,
    )
    response = TestRunCreateResponse.model_validate(run)
    response.stands_without_tests = stands_without_tests
    response.enqueue_errors = enqueue_errors
    return response


@router.post(
    "/preview",
    response_model=TestRunPreviewResponse,
    summary="Предпросмотр состава кампании перед запуском",
    description=(
        "Без побочных эффектов: показывает, какие тесты будут запущены, а "
        "какие пропущены и почему (не «Рабочий» статус, неактивный стенд, "
        "отсутствие в активном составе СТП при `final=True`)."
    ),
    responses={
        403: {"description": "Нет роли с `create` на `test_run`."},
        422: {"description": "У вызывающего нет department_id, либо тело запроса невалидно."},
    },
)
async def preview_test_run(
    body: TestRunCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestRunPreviewResponse:
    stands_without_tests, entries = await svc.preview_test_run(
        db, identity,
        os_version_id=body.os_version_id, mode=body.mode, kernel=body.kernel,
        test_run_stands=body.test_run_stands, final=body.final,
    )
    return TestRunPreviewResponse(stands_without_tests=stands_without_tests, entries=entries)


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
    entries = await entry_repo.list_for_run(db, run_id)
    response.entries = [TestRunEntryResponse.model_validate(entry) for entry in entries]
    states = result_states(items, entries)
    response.progress = {"total": len(states), "attempts": len(items), **dict(Counter(states))}
    current_ids = {item.id for item in latest_attempts(items)}
    logs = await log_availability.for_items(db, items)
    response.queue_items = [
        TestRunQueueItemResponse(
            queue_item_id=item.id,
            log_status=logs[item.id],
            kernel=(item.launch_context or {}).get("KERNEL"),
            stand_id=item.stand_id,
            test_id=item.test_id,
            state=item.state,
            is_retry=item.is_retry,
            retry_of_id=item.retry_of_id,
            test_run_entry_id=item.test_run_entry_id,
            is_current=item.id in current_ids,
            started_at=item.started_at,
            finished_at=item.finished_at,
            error=item.error,
        )
        for item in items
    ]
    return response


@router.get(
    "/{run_id}/summary-comment",
    response_model=RunSummaryCommentResponse,
    summary="Статус end-of-run комментария в Confluence-блоге",
    description=(
        "Отражает попытку публикации/обновления идемпотентного комментария на "
        "Confluence blog-посте релиза после завершения кампании (§2.7, §9.2). "
        "Строки может не быть, если кампания ещё не завершилась терминально — "
        "тогда все поля, кроме `test_run_id`, пустые, это не ошибка."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "Кампания не найдена."},
    },
)
async def get_run_summary_comment(
    run_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
) -> RunSummaryCommentResponse:
    """Get статуса комментария кампании. Любой аутентифицированный актор."""
    result = await run_summary_svc.get_run_summary(db, run_id)
    return RunSummaryCommentResponse.model_validate(result)
