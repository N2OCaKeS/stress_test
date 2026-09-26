"""Internal-эндпоинты очереди для `testing_worker` (§5.5 плана миграции).

Все пути живут вне `/api/testing/v1`, тем же приёмом, что и callback
`prepare-for-test` (см. `internal_prepare_for_test.py`) — чистый
service-to-service канал под shared-secret (`testing_worker` identity в
`SERVICE_API_KEYS`), не часть версионированного публичного API.

Контракт зафиксирован здесь буквально — `testing_worker` строит SSH-исполнение
поверх этих эндпоинтов, без права менять их форму в одностороннем порядке.
Расширять можно (новый эндпоинт, новое опциональное поле с дефолтом), ломать
уже существующую форму — нет.
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.db import get_db
from src.dependencies.internal_auth import require_caller_identity
from src.schemas.common import OkResponse
from src.schemas.queue import (
    QueueClaimResponse,
    QueueCompletedRequest,
    QueueInterruptCheckResponse,
    QueuePreflightStateRequest,
)
from src.services import preflight_status as preflight_status_svc
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
    `claim` того же item'а креды уже не получит), содержимое `dates.conf`
    (`dates_content`/`dates_filename`) для SFTP-записи ДО запуска, уже
    резолвленную команду запуска `starter.sh` (`resolve_dates_content` +
    `resolve_git_token`, см. `services/queue.py::claim_next`),
    `debug_mode`/`is_retry` для контекста воркера.
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

    `interrupted` (`skip`/`pause`) означает, что сессию оборвали по заявке
    оператора: исхода у теста нет, `succeeded`/`exit_code`/`error` не
    рассматриваются, retry не заводится и СТП не обновляется.
    """
    await queue_svc.complete_item(db, queue_item_id, body)
    return OkResponse()


@router.get("/{queue_item_id}/interrupt-check", response_model=QueueInterruptCheckResponse)
async def interrupt_check(
    queue_item_id: str = Path(description="id элемента очереди, полученный из `claim`."),
    db: AsyncSession = Depends(get_db),
    _caller: None = Depends(require_caller_identity("testing_worker")),
) -> QueueInterruptCheckResponse:
    """Просили ли прервать этот элемент, пока он исполняется.

    Воркер дёргает эндпоинт параллельно с идущей SSH-сессией, по таймеру
    (`INTERRUPT_POLL_INTERVAL_SECONDS`). Обычный `SELECT` без лока — заявка
    выставляется публичными `/skip` и `/pause` и снимается тем же
    `/completed`, которым воркер отчитывается о прерывании.
    """
    return QueueInterruptCheckResponse(
        action=await queue_svc.get_interrupt_action(db, queue_item_id),
    )


@router.post("/{queue_item_id}/preflight-state", response_model=OkResponse)
async def preflight_state(
    body: QueuePreflightStateRequest,
    queue_item_id: str = Path(description="id элемента очереди, для которого ждём внешние сервисы."),
    db: AsyncSession = Depends(get_db),
    _caller: None = Depends(require_caller_identity("testing_worker")),
) -> OkResponse:
    """testing_worker сообщает, что ждёт внешние сервисы перед запуском item'а.

    `waiting` — записать/продлить ожидание (TTL — два интервала опроса отдела),
    `ok` — снять. Отдел берётся из стенда item'а. Неизвестный item — 404.
    """
    await preflight_status_svc.set_state(
        db, queue_item_id, waiting=body.state == "waiting", unavailable=body.unavailable,
    )
    return OkResponse()
