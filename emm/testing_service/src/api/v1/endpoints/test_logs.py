"""Чтение/экспорт логов прогонов (§2.6, §8.4 плана миграции).

Открыто любому аутентифицированному актору (`AuthenticatedIdentity`) — то же
соображение, что и у остальных read-путей сервиса (каталог глобальных
переменных, список стендов): чтение лога не секрет и не завязано на
department-scope роль. Если позже понадобится более строгий гейт — заводить
его отдельным решением, не задним числом здесь.
"""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AuthenticatedIdentity
from src.dependencies.db import get_db
from src.schemas.common import PaginatedResponse
from src.schemas.test_log import TestLogSegmentResponse
from src.services import test_log as svc

router = APIRouter(prefix="/queue-items")


@router.get(
    "/{queue_item_id}/log",
    summary="Скачать/просмотреть текст лога прогона",
    description=(
        "Без `from`/`to` — весь текст лога, отдаётся как attachment с "
        "человекочитаемым именем файла (§8.3: "
        "`<stand>_<testname>_<os_version>_<kernel>_<date>.log`). С одним или "
        "обоими параметрами — диапазон текста по офсетам "
        "(`test_log_segments.byte_offset_*`) — для клика по сегменту в UI. "
        "Невалидный диапазон (`from>to`, за пределами длины текста) — 422."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "TEST_LOG_NOT_FOUND — для этого queue_item лога ещё нет."},
        422: {"description": "LOG_RANGE_INVALID — невалидный from/to."},
    },
)
async def get_log(
    queue_item_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    from_: int | None = Query(default=None, ge=0, alias="from", description="Начало диапазона (включительно)."),
    to_: int | None = Query(default=None, ge=0, alias="to", description="Конец диапазона (исключая)."),
) -> Response:
    """Get текста лога, целиком или диапазоном. Любой аутентифицированный актор."""
    log, content = await svc.get_log_range(db, queue_item_id, from_, to_)
    filename = await svc.build_filename(db, log)
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{queue_item_id}/log/segments",
    response_model=PaginatedResponse[TestLogSegmentResponse],
    summary="Список чекпоинтов/команд лога",
    description=(
        "Метаданные сегментов без самого текста — офсеты для перехода к "
        "диапазону через `GET .../log?from=&to=`. `status` фильтрует "
        "(например `FATAL` — сразу видно, где упало, без чтения всего лога)."
    ),
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        404: {"description": "TEST_LOG_NOT_FOUND — для этого queue_item лога ещё нет."},
    },
)
async def list_log_segments(
    queue_item_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(
        default=None, alias="status", description="Фильтр по статусу сегмента: OK / CHANGED / FATAL.",
    ),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[TestLogSegmentResponse]:
    """List сегментов лога. Любой аутентифицированный актор."""
    items, total = await svc.list_segments(db, queue_item_id, status=status_filter, limit=limit, offset=offset)
    return PaginatedResponse[TestLogSegmentResponse](
        items=[TestLogSegmentResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )
