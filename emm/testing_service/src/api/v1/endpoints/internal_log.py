"""Internal-приёмник структурированных логов от `testing_worker` (§2.6, §8 плана миграции).

Контракт зафиксирован здесь буквально на будущее (переписывание
`testing_worker` на потоковое SSH-исполнение с отправкой сегментов сюда) —
форма тела обоих эндпоинтов не меняется в одностороннем порядке.

* `POST /{queue_item_id}/log-chunk` — сырой инкрементальный кусок вывода ЕЩЁ
  НЕ закрытой команды. Просто аппендит текст в blob лога, НЕ создаёт строку
  `test_log_segments`. Сейчас нужен только для накопления (§8.6 — живой
  просмотр во время исполнения появится позже, поверх этого же
  накопленного текста).
* `POST /{queue_item_id}/log-segment` — один уже завершённый шаг (checkpoint
  или command) с готовым результатом. `testing_service` сам форматирует блок
  по легаси-шаблону (`services/test_log.py::format_block` — единый источник
  истины формата, не дублируется на стороне воркера), аппендит готовый текст
  в тот же blob и одновременно вставляет строку `test_log_segments` с
  офсетами внутри уже выросшего к этому моменту blob'а.

Оба эндпоинта лениво создают `test_logs`, если для `queue_item_id` его ещё
нет — не важно, что вызвано первым, чанк или сегмент (см.
`services/test_log.py::get_or_create_log`).

`include_in_schema=False`, вне `/api/testing/v1` — тот же приём, что и
`internal_queue.py`/`internal_prepare_for_test.py`: чистый service-to-service
канал под `X-Service-Identity: testing_worker` + shared-secret, а не часть
версионированного публичного API.
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.db import get_db
from src.dependencies.internal_auth import require_caller_identity
from src.schemas.test_log import LogAppendResponse, LogChunkRequest, LogSegmentRequest
from src.services import test_log as test_log_svc

router = APIRouter(prefix="/internal/queue", include_in_schema=False)


@router.post("/{queue_item_id}/log-chunk", response_model=LogAppendResponse)
async def log_chunk(
    body: LogChunkRequest,
    queue_item_id: str = Path(description="id элемента очереди, полученный из `claim`."),
    db: AsyncSession = Depends(get_db),
    _caller: None = Depends(require_caller_identity("testing_worker")),
) -> LogAppendResponse:
    """Накопить сырой вывод ещё выполняющейся команды. Не создаёт сегмент."""
    log = await test_log_svc.append_chunk(db, queue_item_id, body.text)
    return LogAppendResponse(log_id=log.id)


@router.post("/{queue_item_id}/log-segment", response_model=LogAppendResponse)
async def log_segment(
    body: LogSegmentRequest,
    queue_item_id: str = Path(description="id элемента очереди, полученный из `claim`."),
    db: AsyncSession = Depends(get_db),
    _caller: None = Depends(require_caller_identity("testing_worker")),
) -> LogAppendResponse:
    """Зафиксировать один завершённый шаг (checkpoint/command) целиком."""
    log = await test_log_svc.append_segment(db, queue_item_id, body)
    return LogAppendResponse(log_id=log.id)
