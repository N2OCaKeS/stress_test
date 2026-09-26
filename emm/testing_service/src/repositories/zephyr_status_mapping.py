"""ZephyrStatusMapping-репозиторий — сырой CRUD против `zephyr_status_mappings`."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ZephyrStatusMapping


async def list_for_department(db: AsyncSession, department_id: str | None) -> list[ZephyrStatusMapping]:
    """Строки отдела; `department_id=None` — набор по умолчанию."""
    column = ZephyrStatusMapping.department_id
    cond = column.is_(None) if department_id is None else column == department_id
    stmt = select(ZephyrStatusMapping).where(cond).order_by(ZephyrStatusMapping.zephyr_status)
    return list((await db.execute(stmt)).scalars().all())


async def delete_for_department(db: AsyncSession, department_id: str) -> int:
    stmt = delete(ZephyrStatusMapping).where(ZephyrStatusMapping.department_id == department_id)
    result = await db.execute(stmt)
    return result.rowcount or 0


async def create(db: AsyncSession, data: dict) -> ZephyrStatusMapping:
    obj = ZephyrStatusMapping(**data)
    db.add(obj)
    await db.flush()
    return obj
