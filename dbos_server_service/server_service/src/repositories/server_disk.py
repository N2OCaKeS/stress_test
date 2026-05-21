"""ServerDisk-репозиторий — сырой CRUD против таблицы `server_disks`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ServerDisk


async def get_by_id(db: AsyncSession, disk_id: str) -> ServerDisk | None:
    """SELECT по PK. Visibility-check (через server) делает service-layer."""
    stmt = select(ServerDisk).where(ServerDisk.id == disk_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_server_and_device(
    db: AsyncSession, server_id: str, device_name: str,
) -> ServerDisk | None:
    """SELECT по композитному UNIQUE (server_id, device_name).

    Используется inventory-callback'ом для upsert'а: ищем существующий
    диск, чтобы обновить его, либо INSERT'им новый.
    """
    stmt = select(ServerDisk).where(
        ServerDisk.server_id == server_id,
        ServerDisk.device_name == device_name,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_for_server(
    db: AsyncSession, server_id: str, limit: int = 100, offset: int = 0,
) -> list[ServerDisk]:
    """Список дисков одного сервера, упорядочен по created_at DESC."""
    stmt = (
        select(ServerDisk)
        .where(ServerDisk.server_id == server_id)
        .order_by(ServerDisk.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_for_server(db: AsyncSession, server_id: str) -> int:
    """COUNT для пагинации."""
    stmt = select(func.count(ServerDisk.id)).where(ServerDisk.server_id == server_id)
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> ServerDisk:
    """INSERT новой строки. commit — на caller'е."""
    obj = ServerDisk(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: ServerDisk, changes: dict) -> ServerDisk:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: ServerDisk) -> None:
    """DELETE объекта. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()
