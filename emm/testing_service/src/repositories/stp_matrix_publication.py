"""StpMatrixPublication-репозиторий — одна строка на `(department_id, os_version_id)`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import StpMatrixPublication


async def get_by_department_and_os_version(
    db: AsyncSession, department_id: str, os_version_id: str,
) -> StpMatrixPublication | None:
    stmt = select(StpMatrixPublication).where(
        StpMatrixPublication.department_id == department_id,
        StpMatrixPublication.os_version_id == os_version_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> StpMatrixPublication:
    obj = StpMatrixPublication(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(
    db: AsyncSession, obj: StpMatrixPublication, changes: dict,
) -> StpMatrixPublication:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
