"""Internal-эндпоинты очереди для `testing_worker` (§5.5 плана миграции).

Оба пути живут вне `/api/testing/v1`, тем же приёмом, что и callback
`prepare-for-test` (см. `internal_prepare_for_test.py`) — чистый
service-to-service канал под shared-secret (`testing_worker` identity в
`SERVICE_API_KEYS`), не часть версионированного публичного API.

Контракт зафиксирован здесь буквально — следующий агент (реализация
`testing_worker`) строит SSH-исполнение поверх ЭТИХ двух эндпоинтов, без
права менять их форму в одностороннем порядке.
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.db import get_db
from src.dependencies.internal_auth import require_caller_identity
from src.schemas.common import OkResponse
from src.schemas.queue import QueueClaimResponse, QueueCompletedRequest
from src.services import queue as queue_svc

router = APIRouter(prefix="/internal/queue", include_in_schema=False)


@router.post("/claim", response_model=QueueClaimResponse)
async def claim(
    db: AsyncSession = Depends(get_db),
    _caller: None = Depends(require_caller_identity("testing_worker")),
) -> QueueClaimResponse:
    """Атомарно забрать один готовый элемент очереди (по любому стенду).

    `SELECT ... FOR UPDATE SKIP LOCKED` по `state='ready'`, ORDER BY
    `position`, `created_at` — конкурентные вызовы разных воркер-процессов не
    получают один и тот же item дважды. `item=null` — очередь пуста, воркер
    продолжает поллинг.

    Ответ несёт всё необходимое для одной SSH-сессии: `host` стенда, креды
    тестового пользователя (одноразовое чтение из Redis-стэша — второй вызов
    `claim` того же item'а креды уже не получит), уже резолвленную команду
    (`resolve_command`), `debug_mode`/`is_retry` для контекста воркера.
    """
    item = await queue_svc.claim_next(db)
    return QueueClaimResponse(item=item)


@router.post("/{queue_item_id}/completed", response_model=OkResponse)
async def completed(
    body: QueueCompletedRequest,
    queue_item_id: str = Path(description="id элемента очереди, полученный из `claim`."),
    db: AsyncSession = Depends(get_db),
    _caller: None = Depends(require_caller_identity("testing_worker")),
) -> OkResponse:
    """testing_worker сообщает исход SSH-исполнения.

    Только факт успеха/провала + exit_code — без полного лога (потоковое
    сохранение логов появится волной 6). На успехе — `succeeded`, очередь
    стенда продолжается следующим `queued`-item'ом (или бронь снимается, если
    это был последний). На провале — та же retry-логика, что и у callback'а
    `prepare-for-test`.

    Дубль уже обработанного completion'а (`state` уже не `running`) —
    идемпотентный no-op.
    """
    await queue_svc.complete_item(db, queue_item_id, body)
    return OkResponse()
