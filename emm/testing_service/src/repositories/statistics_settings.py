"""StatisticsSettings-репозиторий — сырой CRUD против singleton-строки."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.statistics_settings import SINGLETON_ID, StatisticsSettings


async def get_singleton(db: AsyncSession) -> StatisticsSettings | None:
    """SELECT singleton-строки. `None` — настройки ещё не сохранялись ни разу."""
    return await db.get(StatisticsSettings, SINGLETON_ID)


async def upsert(db: AsyncSession, changes: dict) -> StatisticsSettings:
    """Создать строку при первом вызове, иначе in-place обновить поля. commit — на caller'е."""
    row = await get_singleton(db)
    if row is None:
        row = StatisticsSettings(id=SINGLETON_ID)
        db.add(row)
    for key, value in changes.items():
        setattr(row, key, value)
    await db.flush()
    return row
