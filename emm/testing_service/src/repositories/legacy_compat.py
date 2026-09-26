"""Репозиторий compat `/rest/api/*`: подсети и singleton настроек."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.legacy_compat import SINGLETON_ID, CompatAllowedNetwork, LegacyCompatSettings


async def list_networks(db: AsyncSession, *, enabled_only: bool = False) -> list[CompatAllowedNetwork]:
    """Подсети в порядке создания (сид первым)."""
    stmt = select(CompatAllowedNetwork).order_by(
        CompatAllowedNetwork.created_at.asc(), CompatAllowedNetwork.cidr.asc(),
    )
    if enabled_only:
        stmt = stmt.where(CompatAllowedNetwork.enabled.is_(True))
    return list((await db.execute(stmt)).scalars())


async def get_network(db: AsyncSession, network_id: str) -> CompatAllowedNetwork | None:
    return await db.get(CompatAllowedNetwork, network_id)


async def create_network(db: AsyncSession, data: dict) -> CompatAllowedNetwork:
    """INSERT. commit — на caller'е."""
    obj = CompatAllowedNetwork(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update_network(db: AsyncSession, obj: CompatAllowedNetwork, changes: dict) -> CompatAllowedNetwork:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete_network(db: AsyncSession, obj: CompatAllowedNetwork) -> None:
    await db.delete(obj)
    await db.flush()


async def get_settings(db: AsyncSession) -> LegacyCompatSettings | None:
    """Singleton-строка. `None` — настройки ни разу не сохранялись (миграция её сеет)."""
    return await db.get(LegacyCompatSettings, SINGLETON_ID)


async def upsert_settings(db: AsyncSession, changes: dict) -> LegacyCompatSettings:
    """Создать строку при первом вызове, иначе обновить поля. commit — на caller'е."""
    row = await get_settings(db)
    if row is None:
        row = LegacyCompatSettings(id=SINGLETON_ID)
        db.add(row)
    for key, value in changes.items():
        setattr(row, key, value)
    await db.flush()
    return row
