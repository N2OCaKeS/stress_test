"""OsVersion-репозиторий — сырой CRUD против таблицы `os_versions`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import OsVersion, Server


async def get_by_id(db: AsyncSession, os_version_id: str) -> OsVersion | None:
    """SELECT по PK."""
    stmt = select(OsVersion).where(OsVersion.id == os_version_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_name(db: AsyncSession, name: str) -> OsVersion | None:
    """SELECT по UNIQUE name. Используется inventory-callback'ом."""
    stmt = select(OsVersion).where(OsVersion.name == name)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_all(
    db: AsyncSession, limit: int = 100, offset: int = 0,
) -> list[OsVersion]:
    """Полный список OS-версий (каталог глобальный), order by discovered_at DESC."""
    stmt = (
        select(OsVersion)
        .order_by(OsVersion.discovered_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_all(db: AsyncSession) -> int:
    """COUNT всего каталога — для total в pagination."""
    stmt = select(func.count(OsVersion.id))
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> OsVersion:
    """INSERT новой строки. commit — на caller'е."""
    obj = OsVersion(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: OsVersion, changes: dict) -> OsVersion:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: OsVersion) -> None:
    """DELETE объекта. commit — на caller'е. FK ondelete=RESTRICT от servers.os_version_id."""
    await db.delete(obj)
    await db.flush()


async def count_referencing_servers(db: AsyncSession, os_version_id: str) -> int:
    """COUNT серверов, ссылающихся на OS-версию через `servers.os_version_id`.

    Используется для pre-check'а перед удалением: SQLAlchemy без
    `passive_deletes=True` обнуляет дочерние FK сам, и DB-уровневый
    RESTRICT не срабатывает. Явный count даёт детерминированный 409
    ещё до DELETE.
    """
    stmt = select(func.count(Server.id)).where(Server.os_version_id == os_version_id)
    return int((await db.execute(stmt)).scalar_one())
