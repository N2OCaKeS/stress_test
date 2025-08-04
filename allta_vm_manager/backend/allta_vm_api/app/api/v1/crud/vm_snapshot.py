from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.models.vm_snapshot import VMSnapshot
from app.api.v1.schemas.vm_snapshot import VMSnapshotCreate, VMSnapshotUpdate


async def get_snapshot(db: AsyncSession, snapshot_id: int) -> Optional[VMSnapshot]:
    result = await db.execute(
        select(VMSnapshot).where(VMSnapshot.id == snapshot_id)
    )
    return result.scalars().first()


async def get_snapshots(db: AsyncSession, vm_id: Optional[int] = None) -> List[VMSnapshot]:
    stmt = select(VMSnapshot)
    if vm_id is not None:
        stmt = stmt.where(VMSnapshot.vm_id == vm_id)
    result = await db.execute(stmt)
    return result.scalars().all()


async def create_snapshot(db: AsyncSession, data: VMSnapshotCreate) -> VMSnapshot:
    snapshot = VMSnapshot(**data.model_dump())
    db.add(snapshot)
    try:
        await db.commit()
        await db.refresh(snapshot)
    except IntegrityError as e:
        await db.rollback()
        raise ValueError(f"Failed to create snapshot: {str(e)}")
    return snapshot


async def update_snapshot(db: AsyncSession, snapshot_id: int, data: VMSnapshotUpdate) -> Optional[VMSnapshot]:
    snapshot = await get_snapshot(db, snapshot_id)
    if not snapshot:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(snapshot, field, value)

    try:
        await db.commit()
        await db.refresh(snapshot)
    except IntegrityError as e:
        await db.rollback()
        raise ValueError(f"Failed to update snapshot: {str(e)}")
    return snapshot


async def delete_snapshot(db: AsyncSession, snapshot_id: int) -> bool:
    snapshot = await get_snapshot(db, snapshot_id)
    if not snapshot:
        return False

    await db.delete(snapshot)
    await db.commit()
    return True
