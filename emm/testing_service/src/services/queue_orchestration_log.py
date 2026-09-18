"""Диагностика диспетчера очереди — «почему поллинг не привёл к запуску теста».

`record()`/`record_once()` — единственные точки записи, вызываются из
`services/queue.py` там, где диспетчер реально пытался продвинуть очередь и
не смог, и из `check_stuck_items()`/`check_claim_desync()` — периодических
проверок, подключенных к `services/queue.py::claim_next` (см. его докстринг).

Запись идёт в SAVEPOINT (`db.begin_nested()`), не в отдельном commit'е —
`queue.py` местами держит `SELECT ... FOR UPDATE` на строке, которую ещё не
закончил обрабатывать (например `handle_prepare_completed`), и ранний commit
изнутри `record()` снял бы эту блокировку раньше времени. SAVEPOINT можно
безопасно открыть и закрыть посреди уже идущей транзакции, не трогая её саму
— коммит транзакции целиком остаётся на вызывающем коде, как и у остальных
репозиториев этого слоя. Единственное исключение — `claim_next()` на ветке
"очередь пуста": там дальше ничего не коммитит, поэтому коммитит сам сразу
после проверок.

Всё здесь best-effort в том же смысле, что `run_summary`/`statistics_recalc`
в `queue.py::_maybe_post_run_summary`: сбой записи диагностики не должен
ронять сам диспетчер, поэтому `record()` глотает исключения и просто логирует
их через `logger.warning`.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    CLAIM_DESYNC_GRACE_SECONDS,
    QUEUE_STUCK_THRESHOLD_SECONDS,
    QueueOrchestrationEventKind,
)
from src.models import QueueOrchestrationEvent
from src.repositories import queue_orchestration_event as repo
from src.utils.ids import queue_orchestration_event_id as new_id

logger = logging.getLogger(__name__)

# Сколько последних событий держим на стенд. Оперативная диагностика "что
# происходило последние часы", не аудит — копить историю месяцами незачем.
_MAX_EVENTS_PER_STAND = 200


async def record(
    db: AsyncSession,
    stand_id: str,
    kind: str,
    *,
    queue_item_id: str | None = None,
    detail: str | None = None,
) -> None:
    """Записать одно содержательное событие + подрезать историю этого стенда.

    Пишет в SAVEPOINT, commit — на вызывающем коде (см. module docstring).
    """
    try:
        async with db.begin_nested():
            await repo.create(db, {
                "id": new_id(),
                "stand_id": stand_id,
                "queue_item_id": queue_item_id,
                "kind": kind,
                "detail": (detail or "")[:2048] or None,
            })
            await repo.trim_for_stand(db, stand_id, keep=_MAX_EVENTS_PER_STAND)
    except Exception as exc:  # noqa: BLE001 — best-effort, не должно ронять диспетчер очереди
        logger.warning(
            "queue_orchestration_log.record failed for stand %s kind %s: %s",
            stand_id, kind, exc,
        )


async def record_once(
    db: AsyncSession,
    stand_id: str,
    kind: str,
    queue_item_id: str,
    *,
    detail: str | None = None,
) -> None:
    """Как `record()`, но не дублирует событие, если для этого item'а такое уже есть.

    Нужен кindам, которые обнаруживаются повторно на каждый следующий
    поллинг, пока условие не исчезнет (`item_stuck_in_head`,
    `claim_found_nothing_with_queue`) — без дедупа один и тот же item плодил
    бы новую строку каждые несколько секунд.
    """
    try:
        if await repo.exists_for_item(db, queue_item_id, kind):
            return
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "queue_orchestration_log.record_once dedup check failed for item %s kind %s: %s",
            queue_item_id, kind, exc,
        )
        return
    await record(db, stand_id, kind, queue_item_id=queue_item_id, detail=detail)


async def list_for_stand(db: AsyncSession, stand_id: str, *, limit: int = 100) -> list[QueueOrchestrationEvent]:
    """Последние события стенда для `GET /test-stands/{id}/orchestration-log`."""
    return await repo.list_for_stand(db, stand_id, limit=limit)


async def check_stuck_items(db: AsyncSession) -> None:
    """Головные item'ы, не продвинувшиеся дольше `QUEUE_STUCK_THRESHOLD_SECONDS`.

    Вызывается из `services/queue.py::claim_next` на каждый проход —
    testing_worker и так дёргает `claim` каждые несколько секунд
    (`QUEUE_POLL_INTERVAL_SECONDS` на его стороне), это уже готовый
    периодический тик, отдельный background-loop заводить незачем.
    """
    for item in await repo.find_stuck_active_items(db, threshold_seconds=QUEUE_STUCK_THRESHOLD_SECONDS):
        await record_once(
            db, item.stand_id, QueueOrchestrationEventKind.ITEM_STUCK_IN_HEAD, item.id,
            detail=f"item stuck in state={item.state} for over {QUEUE_STUCK_THRESHOLD_SECONDS}s",
        )


async def check_claim_desync(db: AsyncSession) -> None:
    """Вызывается только когда `claim_next_ready()` в этом же проходе вернул `None`.

    Отдельная функция от `check_stuck_items()`, а не один общий тик: этот
    признак осмыслен ровно в момент "мы только что не нашли ничего забрать" —
    считать его раньше вызова `claim_next_ready()` означало бы делать вывод
    до того, как он вообще что-то попытался найти.
    """
    for item in await repo.find_stray_ready_items(db, grace_seconds=CLAIM_DESYNC_GRACE_SECONDS):
        await record_once(
            db, item.stand_id, QueueOrchestrationEventKind.CLAIM_FOUND_NOTHING_WITH_QUEUE, item.id,
            detail="claim_next_ready returned nothing while a ready item was still present",
        )
