"""VmImage-репозиторий — каталог боксов-образов + резолв box→url."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import VmImage
from src.utils.ids import vm_image_id


async def get_by_name(
    db: AsyncSession, name: str, hub_server_id: str | None
) -> VmImage | None:
    """SELECT образа по (name, hub_server_id). NULL hub — глобальный."""
    stmt = select(VmImage).where(VmImage.name == name)
    if hub_server_id is None:
        stmt = stmt.where(VmImage.hub_server_id.is_(None))
    else:
        stmt = stmt.where(VmImage.hub_server_id == hub_server_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def resolve(
    db: AsyncSession, name: str, hub_server_id: str
) -> VmImage | None:
    """Найти образ для create: сначала hub-специфичный, потом глобальный.

    Hub-привязанная запись имеет приоритет над глобальной с тем же именем
    (позволяет переопределить URL образа для конкретного hub'а).
    """
    specific = await get_by_name(db, name, hub_server_id)
    if specific is not None:
        return specific
    return await get_by_name(db, name, None)


async def list_all(
    db: AsyncSession, *, limit: int, offset: int
) -> list[VmImage]:
    """Каталог образов (name ASC)."""
    stmt = select(VmImage).order_by(VmImage.name, VmImage.hub_server_id).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def count_all(db: AsyncSession) -> int:
    """COUNT каталога для пагинации."""
    return int((await db.execute(select(func.count(VmImage.id)))).scalar_one())


async def sync_global(db: AsyncSession, name: str, fields: dict) -> bool:
    """Завести/обновить глобальный образ по имени. True — создан, False — обновлён.

    Refresh каталога — сериализованная admin-операция (не гоночная), поэтому
    read-then-write здесь безопасен и даёт точный счётчик created/updated.
    Без commit — owner транзакции caller.
    """
    existing = await get_by_name(db, name, None)
    if existing is None:
        obj = VmImage(id=vm_image_id(), name=name, hub_server_id=None, **fields)
        db.add(obj)
        await db.flush()
        return True
    for key, value in fields.items():
        setattr(existing, key, value)
    await db.flush()
    return False
