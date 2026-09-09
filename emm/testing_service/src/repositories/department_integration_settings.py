"""DepartmentIntegrationSettings-репозиторий — одна строка на `department_id`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DepartmentIntegrationSettings


async def get_by_department(db: AsyncSession, department_id: str) -> DepartmentIntegrationSettings | None:
    stmt = select(DepartmentIntegrationSettings).where(
        DepartmentIntegrationSettings.department_id == department_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> DepartmentIntegrationSettings:
    obj = DepartmentIntegrationSettings(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(
    db: AsyncSession, obj: DepartmentIntegrationSettings, changes: dict,
) -> DepartmentIntegrationSettings:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
