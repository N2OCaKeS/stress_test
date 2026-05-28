"""ServerDisk-репозиторий — сырой CRUD против таблицы `server_disks`.

Диски управляются только через карточку сервера (раздел `storage`), поэтому
наружу выставлены list/sync-примитивы, а не пагинируемый CRUD.
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ServerDisk


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


async def list_all_for_server(db: AsyncSession, server_id: str) -> list[ServerDisk]:
    """Все диски сервера, упорядоченные по device_name (стабильный порядок storage)."""
    stmt = (
        select(ServerDisk)
        .where(ServerDisk.server_id == server_id)
        .order_by(ServerDisk.device_name.asc())
    )
    return list((await db.execute(stmt)).scalars())


async def create(db: AsyncSession, data: dict) -> ServerDisk:
    """INSERT новой строки. commit — на caller'е."""
    obj = ServerDisk(**data)
    db.add(obj)
    await db.flush()
    return obj


async def upsert_by_device(db: AsyncSession, data: dict) -> ServerDisk:
    """INSERT … ON CONFLICT (server_id, device_name) DO UPDATE — атомарный upsert.

    Гарантирует идемпотентность при гонке двух callback'ов от worker'а на
    одну (server_id, device_name) пару — конфликт по `uq_server_disk_device`
    обрабатывается в одном SQL-запросе, без savepoint'а и без IntegrityError.
    `updated_at` принудительно тыкается `now()` (onupdate=func.now() в ORM
    при ON CONFLICT не срабатывает).
    """
    stmt = (
        pg_insert(ServerDisk)
        .values(**data)
        .on_conflict_do_update(
            constraint="uq_server_disk_device",
            set_={
                "size_gb": data["size_gb"],
                "model": data["model"],
                "is_system": data["is_system"],
                "updated_at": func.now(),
            },
        )
        .returning(ServerDisk)
    )
    obj = (await db.execute(stmt)).scalar_one()
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
