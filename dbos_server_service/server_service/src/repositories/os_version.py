"""OsVersion-репозиторий — сырой CRUD против таблицы `os_versions`."""

from datetime import datetime

from sqlalchemy import and_, func, or_, select
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


async def list_all_after(
    db: AsyncSession,
    *,
    limit: int,
    after_discovered_at: datetime | None,
    after_id: str | None,
) -> list[OsVersion]:
    """Keyset-страница каталога по `(discovered_at DESC, id DESC)`.

    Сортировка та же, что в `list_all` — `discovered_at DESC` — плюс tie-break
    по `id`, чтобы курсор не «соскальзывал» на коллизиях timestamp'а.
    """
    stmt = (
        select(OsVersion)
        .order_by(OsVersion.discovered_at.desc(), OsVersion.id.desc())
        .limit(limit)
    )
    if after_discovered_at is not None and after_id is not None:
        stmt = stmt.where(
            or_(
                OsVersion.discovered_at < after_discovered_at,
                and_(
                    OsVersion.discovered_at == after_discovered_at,
                    OsVersion.id < after_id,
                ),
            )
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
