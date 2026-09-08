"""ServerCategory-репозиторий — сырой CRUD против таблицы `server_categories`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Server, ServerCategory


async def get_by_id(db: AsyncSession, category_id: str) -> ServerCategory | None:
    """SELECT по PK."""
    stmt = select(ServerCategory).where(ServerCategory.id == category_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> ServerCategory | None:
    """SELECT по UNIQUE code."""
    stmt = select(ServerCategory).where(ServerCategory.code == code)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_all(
    db: AsyncSession, limit: int = 100, offset: int = 0,
) -> list[ServerCategory]:
    """Полный список категорий, order by code — каталог маленький и стабильный."""
    stmt = (
        select(ServerCategory)
        .order_by(ServerCategory.code.asc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_all(db: AsyncSession) -> int:
    """COUNT всего каталога — для total в pagination."""
    stmt = select(func.count(ServerCategory.id))
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> ServerCategory:
    """INSERT новой строки. commit — на caller'е."""
    obj = ServerCategory(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: ServerCategory, changes: dict) -> ServerCategory:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: ServerCategory) -> None:
    """DELETE объекта. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()


async def count_referencing_servers(db: AsyncSession, category_id: str) -> int:
    """COUNT серверов, ссылающихся на категорию через `servers.category_id`.

    Тот же pre-check, что у os_version: SQLAlchemy без `passive_deletes=True`
    обнуляет дочерние FK сам, DB-уровневый RESTRICT не срабатывает, и без
    явного count'а удаление «используемой» категории прошло бы молча.
    """
    stmt = select(func.count(Server.id)).where(Server.category_id == category_id)
    return int((await db.execute(stmt)).scalar_one())
