"""StatisticsCategory-репозиторий — сырой CRUD против `statistics_categories`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StatisticsCategory


async def get_by_id(db: AsyncSession, category_id: str) -> StatisticsCategory | None:
    """SELECT по PK."""
    return await db.get(StatisticsCategory, category_id)


async def list_all(db: AsyncSession, *, enabled_only: bool = False) -> list[StatisticsCategory]:
    """Весь справочник в порядке модалки (`sort_order`, затем `key` для стабильности)."""
    stmt = select(StatisticsCategory).order_by(
        StatisticsCategory.sort_order.asc(), StatisticsCategory.key.asc(),
    )
    if enabled_only:
        stmt = stmt.where(StatisticsCategory.enabled.is_(True))
    return list((await db.execute(stmt)).scalars())


async def list_by_keys(db: AsyncSession, keys: list[str]) -> list[StatisticsCategory]:
    """Строки с этими ключами (включая выключенные) в порядке справочника."""
    if not keys:
        return []
    stmt = (
        select(StatisticsCategory)
        .where(StatisticsCategory.key.in_(keys))
        .order_by(StatisticsCategory.sort_order.asc(), StatisticsCategory.key.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> StatisticsCategory:
    """INSERT новой строки. commit — на caller'е."""
    obj = StatisticsCategory(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: StatisticsCategory, changes: dict) -> StatisticsCategory:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: StatisticsCategory) -> None:
    """DELETE объекта. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()
