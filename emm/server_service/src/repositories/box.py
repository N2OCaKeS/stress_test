"""Box-репозиторий — CRUD каталога боксов-заготовок отдела (boxes)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Box


async def get_by_id(db: AsyncSession, box_id: str) -> Box | None:
    """SELECT бокса по PK."""
    return (
        await db.execute(select(Box).where(Box.id == box_id))
    ).scalar_one_or_none()


async def list_in_department(
    db: AsyncSession, department_id: str, *, limit: int = 100, offset: int = 0,
) -> list[Box]:
    """Боксы отдела (created_at DESC)."""
    stmt = (
        select(Box)
        .where(Box.department_id == department_id)
        .order_by(Box.created_at.desc(), Box.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_in_department(db: AsyncSession, department_id: str) -> int:
    """COUNT боксов отдела — для total в pagination."""
    stmt = select(func.count(Box.id)).where(Box.department_id == department_id)
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> Box:
    """INSERT бокса (без commit — owner транзакции caller)."""
    obj = Box(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: Box, changes: dict) -> Box:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: Box) -> None:
    """DELETE строки бокса (без commit)."""
    await db.delete(obj)
    await db.flush()
