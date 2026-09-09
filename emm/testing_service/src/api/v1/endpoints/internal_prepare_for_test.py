"""Приёмник callback'а `prepare-for-test` от server_service (§5.1 плана миграции).

Путь — `/internal/prepare-for-test/{prepare_request_id}/completed`, БЕЗ
`/api/testing/v1` префикса: server_service шлёт его буквально по этому пути
(`server_service/src/services/testing_client.py::callback_path`) — контракт
уже закоммичен на той стороне, менять его в одностороннем порядке нельзя.
Роутер поэтому подключается напрямую к `app` в `main.py`, а не через
`api_router` (см. `src/api/internal_router.py`).
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.db import get_db
from src.dependencies.internal_auth import require_caller_identity
from src.schemas.common import OkResponse
from src.schemas.queue import PrepareForTestCompletedCallback
from src.services import queue as queue_svc

router = APIRouter(prefix="/internal/prepare-for-test", include_in_schema=False)


@router.post("/{prepare_request_id}/completed", response_model=OkResponse)
async def prepare_for_test_completed(
    body: PrepareForTestCompletedCallback,
    prepare_request_id: str = Path(description="`prep_<hex>` из 202-ответа server_service."),
    db: AsyncSession = Depends(get_db),
    _caller: None = Depends(require_caller_identity("server_service")),
) -> OkResponse:
    """server_service сообщает исход подготовки стенда под тест.

    На успехе — креды тестового пользователя стэшатся в Redis, элемент
    очереди переходит в `ready` (заберёт `testing_worker` через
    `POST /internal/queue/claim`). На провале — retry-логика
    (`department_test_settings.retry_enabled`), и если это был последний
    активный элемент очереди стенда — `release-for-service`.

    Неизвестный `prepare_request_id` — 404: server_service не входит в
    retryable-набор статусов на 4xx, повторной доставки не будет.
    Callback по уже обработанному запросу (state уже не `preparing`) —
    идемпотентный no-op, `{"ok": true}`.
    """
    await queue_svc.handle_prepare_completed(db, prepare_request_id, body)
    return OkResponse()
