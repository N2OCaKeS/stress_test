"""ZephyrFolder-репозиторий — сырой CRUD против таблицы `zephyr_folders`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ZephyrFolder


async def get_by_department_and_os_version(
    db: AsyncSession, department_id: str, os_version_id: str,
) -> ZephyrFolder | None:
    stmt = select(ZephyrFolder).where(
        ZephyrFolder.department_id == department_id,
        ZephyrFolder.os_version_id == os_version_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create(db: AsyncSession, data: dict) -> ZephyrFolder:
    obj = ZephyrFolder(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: ZephyrFolder, changes: dict) -> ZephyrFolder:
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj
