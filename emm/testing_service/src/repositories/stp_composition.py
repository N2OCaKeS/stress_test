"""StpComposition-репозиторий — сырой CRUD против таблицы `stp_compositions`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StpComposition


async def get_by_department_and_os_version(
    db: AsyncSession, department_id: str, os_version_id: str,
) -> StpComposition | None:
    stmt = select(StpComposition).where(
        StpComposition.department_id == department_id,
        StpComposition.os_version_id == os_version_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> StpComposition:
    obj = StpComposition(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: StpComposition, changes: dict) -> StpComposition:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
