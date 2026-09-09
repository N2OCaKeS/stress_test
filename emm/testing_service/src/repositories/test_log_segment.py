"""TestLogSegment-репозиторий — append-only список сегментов лога (§8.2, §8.4 плана миграции)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import TestLogSegment


async def create(db: AsyncSession, data: dict) -> TestLogSegment:
    """INSERT новой строки. commit — на caller'е."""
    obj = TestLogSegment(**data)
    db.add(obj)
    await db.flush()
    return obj


async def next_position(db: AsyncSession, log_id: str) -> int:
    """`max(position)+1` среди сегментов лога, либо 0 если сегментов ещё нет."""
    stmt = select(func.max(TestLogSegment.position)).where(TestLogSegment.log_id == log_id)
    current_max = (await db.execute(stmt)).scalar_one_or_none()
    return 0 if current_max is None else current_max + 1


def _apply_status_filter(stmt, *, status: str | None):
    if status is not None:
        stmt = stmt.where(TestLogSegment.status == status)
    return stmt


async def list_by_log(
    db: AsyncSession, log_id: str, *, status: str | None = None, limit: int = 500, offset: int = 0,
) -> list[TestLogSegment]:
    """Страница сегментов лога, по порядку появления, с опциональным фильтром по статусу."""
    stmt = _apply_status_filter(
        select(TestLogSegment).where(TestLogSegment.log_id == log_id), status=status,
    )
    stmt = stmt.order_by(TestLogSegment.position.asc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_by_log(db: AsyncSession, log_id: str, *, status: str | None = None) -> int:
    """COUNT под теми же фильтрами, что и `list_by_log` — для total в pagination."""
    stmt = _apply_status_filter(
        select(func.count(TestLogSegment.id)).where(TestLogSegment.log_id == log_id), status=status,
    )
    return int((await db.execute(stmt)).scalar_one())
