"""StatisticsRecalcState-репозиторий — сырой CRUD против singleton-строки индикатора."""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import StatisticsRecalcStatus
from src.models.statistics_recalc import SINGLETON_ID, StatisticsRecalcState


async def get_singleton(db: AsyncSession) -> StatisticsRecalcState | None:
    """SELECT singleton-строки. `None` — пересчёт ещё ни разу не запускался."""
    return await db.get(StatisticsRecalcState, SINGLETON_ID)


async def mark_running(
    db: AsyncSession, *, triggered_by: str, test_run_id: str | None,
    category: str | None = None, categories: list[str] | None = None,
) -> StatisticsRecalcState:
    """Строка переходит/заводится в `running`. commit — на caller'е.

    `categories` — весь запрошенный набор (NULL — полный пересчёт),
    `category` — первое/текущее семейство из него.
    """
    row = await get_singleton(db)
    if row is None:
        row = StatisticsRecalcState(id=SINGLETON_ID)
        db.add(row)
    row.status = StatisticsRecalcStatus.RUNNING
    row.triggered_by = triggered_by
    row.category = category
    row.categories = categories
    row.test_run_id = test_run_id
    row.started_at = datetime.now(timezone.utc)
    row.finished_at = None
    row.error = None
    await db.flush()
    return row


async def set_current_category(db: AsyncSession, category: str) -> None:
    """При пересчёте нескольких семейств — какое считается сейчас. commit — на caller'е."""
    row = await get_singleton(db)
    if row is None:
        return
    row.category = category
    await db.flush()


async def mark_finished(db: AsyncSession, *, succeeded: bool, error: str | None) -> StatisticsRecalcState:
    """Строка переходит в `succeeded`/`failed`. commit — на caller'е.

    Строки может не быть, если `mark_running` не отработал (гонка/ручное
    удаление строки между двумя фазами фоновой задачи) — заводим её здесь же,
    не роняя фоновую задачу.
    """
    row = await get_singleton(db)
    if row is None:
        row = StatisticsRecalcState(id=SINGLETON_ID)
        db.add(row)
    row.status = StatisticsRecalcStatus.SUCCEEDED if succeeded else StatisticsRecalcStatus.FAILED
    row.finished_at = datetime.now(timezone.utc)
    row.error = error
    await db.flush()
    return row
