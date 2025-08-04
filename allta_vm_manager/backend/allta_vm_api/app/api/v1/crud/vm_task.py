from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.models.vm_task import VMTask
from app.api.v1.schemas.vm_task import VMTaskCreate, VMTaskUpdate


async def get_task(db: AsyncSession, uuid: str) -> Optional[VMTask]:
    result = await db.execute(
        select(VMTask).where(VMTask.uuid == uuid)
    )
    return result.scalars().first()


async def get_tasks(db: AsyncSession, user_id: Optional[int] = None) -> List[VMTask]:
    stmt = select(VMTask)
    if user_id is not None:
        stmt = stmt.where(VMTask.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalars().all()


async def create_task(db: AsyncSession, data: VMTaskCreate) -> VMTask:
    task = VMTask(**data.model_dump())
    db.add(task)
    try:
        await db.commit()
        await db.refresh(task)
    except IntegrityError as e:
        await db.rollback()
        raise ValueError(f"Failed to create task: {str(e)}")
    return task


async def update_task(db: AsyncSession, uuid: str, data: VMTaskUpdate) -> Optional[VMTask]:
    task = await get_task(db, uuid)
    if not task:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(task, field, value)

    try:
        await db.commit()
        await db.refresh(task)
    except IntegrityError as e:
        await db.rollback()
        raise ValueError(f"Failed to update task: {str(e)}")
    return task


async def delete_task(db: AsyncSession, uuid: str) -> bool:
    task = await get_task(db, uuid)
    if not task:
        return False

    await db.delete(task)
    await db.commit()
    return True
