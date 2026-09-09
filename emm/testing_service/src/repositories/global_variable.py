"""GlobalVariable-репозиторий — сырой CRUD против таблицы `global_variables`."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import GlobalVariable


async def get_by_id(db: AsyncSession, variable_id: str) -> GlobalVariable | None:
    """SELECT по PK."""
    stmt = select(GlobalVariable).where(GlobalVariable.id == variable_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> GlobalVariable | None:
    """SELECT по UNIQUE code."""
    stmt = select(GlobalVariable).where(GlobalVariable.code == code)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_all(
    db: AsyncSession, limit: int = 100, offset: int = 0,
) -> list[GlobalVariable]:
    """Страница каталога, order by code — каталог маленький и стабильный."""
    stmt = (
        select(GlobalVariable)
        .order_by(GlobalVariable.code.asc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_all(db: AsyncSession) -> int:
    """COUNT всего каталога — для total в pagination."""
    stmt = select(func.count(GlobalVariable.id))
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> GlobalVariable:
    """INSERT новой строки. commit — на caller'е."""
    obj = GlobalVariable(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: GlobalVariable, changes: dict) -> GlobalVariable:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: GlobalVariable) -> None:
    """DELETE объекта. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()
