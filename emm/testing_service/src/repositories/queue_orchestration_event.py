"""QueueOrchestrationEvent-репозиторий — сырой CRUD против `queue_orchestration_events`."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import QueueItemState
from src.models import QueueItem, QueueOrchestrationEvent


async def create(db: AsyncSession, data: dict) -> QueueOrchestrationEvent:
    """INSERT новой строки. commit — на caller'е."""
    obj = QueueOrchestrationEvent(**data)
    db.add(obj)
    await db.flush()
    return obj


async def exists_for_item(db: AsyncSession, queue_item_id: str, kind: str) -> bool:
    """True, если для этого item'а уже есть событие такого рода — дедуп повторных детекций."""
    stmt = (
        select(QueueOrchestrationEvent.id)
        .where(
            QueueOrchestrationEvent.queue_item_id == queue_item_id,
            QueueOrchestrationEvent.kind == kind,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def list_for_stand(db: AsyncSession, stand_id: str, *, limit: int = 100) -> list[QueueOrchestrationEvent]:
    """Последние события стенда, самые свежие первыми."""
    stmt = (
        select(QueueOrchestrationEvent)
        .where(QueueOrchestrationEvent.stand_id == stand_id)
        .order_by(QueueOrchestrationEvent.created_at.desc(), QueueOrchestrationEvent.id.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def trim_for_stand(db: AsyncSession, stand_id: str, *, keep: int) -> int:
    """Оставить только `keep` самых свежих строк этого стенда, остальные удалить.

    Best-effort ретеншн (не отдельный cron): вызывается сразу после каждой
    записи (`services/queue_orchestration_log.py::record`), поэтому ни разу
    не накапливает больше `keep + 1` строк на стенд.
    """
    stmt = (
        select(QueueOrchestrationEvent.id)
        .where(QueueOrchestrationEvent.stand_id == stand_id)
        .order_by(QueueOrchestrationEvent.created_at.desc(), QueueOrchestrationEvent.id.desc())
        .offset(keep)
    )
    stale_ids = [row[0] for row in (await db.execute(stmt)).all()]
    if not stale_ids:
        return 0
    await db.execute(delete(QueueOrchestrationEvent).where(QueueOrchestrationEvent.id.in_(stale_ids)))
    await db.flush()
    return len(stale_ids)


async def find_stuck_active_items(db: AsyncSession, *, threshold_seconds: int) -> list[QueueItem]:
    """Item'ы в `preparing`/`ready`, чей `updated_at` старше порога.

    `updated_at` — прокси для «сколько времени прошло с момента, как item
    оказался в этом состоянии»: обе интересующие нас записи (переход в
    `preparing` в `_start_or_continue_cycle`, переход в `ready` в
    `handle_prepare_completed`) — обычный `UPDATE` строки, `onupdate=func.now()`
    отрабатывает штатно.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)
    stmt = select(QueueItem).where(
        QueueItem.state.in_((QueueItemState.PREPARING, QueueItemState.READY)),
        QueueItem.updated_at < cutoff,
    )
    return list((await db.execute(stmt)).scalars())


async def find_stray_ready_items(db: AsyncSession, *, grace_seconds: int) -> list[QueueItem]:
    """`ready`-item'ы старше `grace_seconds`, которые всё ещё не забраны `claim`.

    Вызывается только когда `claim_next_ready()` уже вернул `None` в этом же
    проходе — обычный `SELECT` без блокировки, специально не тот же запрос,
    что `claim_next_ready` (там `FOR UPDATE SKIP LOCKED`): здесь важно увидеть
    строку, даже если её кто-то держит залоченной. `grace_seconds` отсекает
    обычную гонку — конкурентный `claim` берёт и коммитит строку за
    миллисекунды, а не секунды.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=grace_seconds)
    stmt = select(QueueItem).where(
        QueueItem.state == QueueItemState.READY,
        QueueItem.updated_at < cutoff,
    )
    return list((await db.execute(stmt)).scalars())
