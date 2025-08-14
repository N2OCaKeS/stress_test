from typing import Optional, Sequence
from sqlalchemy import select, func, update as sa_update, delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.models.vm_snapshot import VMSnapshot

class SnapshotAlreadyExists(Exception):
    pass

class SnapshotNotFound(Exception):
    pass

async def create_snapshot(
    db: AsyncSession,
    *,
    vm_id: int,
    name: str,
) -> VMSnapshot:
    obj = VMSnapshot(vm_id=vm_id, name=name)
    db.add(obj)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise SnapshotAlreadyExists(f"Snapshot with name '{name}' already exists for VM {vm_id}") from e
    await db.commit()
    await db.refresh(obj)
    return obj

async def get_snapshot(db: AsyncSession, *, snapshot_id: int) -> VMSnapshot:
    res = await db.execute(select(VMSnapshot).where(VMSnapshot.id == snapshot_id))
    obj = res.scalar_one_or_none()
    if not obj:
        raise SnapshotNotFound(f"Snapshot {snapshot_id} not found")
    return obj

async def list_snapshots(
    db: AsyncSession,
    *,
    vm_id: Optional[int] = None,
    search: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
    order_by: str = "id",
    desc: bool = False,
) -> tuple[Sequence[VMSnapshot], int]:
    stmt = select(VMSnapshot)
    count_stmt = select(func.count(VMSnapshot.id))

    if vm_id is not None:
        stmt = stmt.where(VMSnapshot.vm_id == vm_id)
        count_stmt = count_stmt.where(VMSnapshot.vm_id == vm_id)

    if search:
        like = f"%{search}%"
        stmt = stmt.where(VMSnapshot.name.ilike(like))
        count_stmt = count_stmt.where(VMSnapshot.name.ilike(like))

    # сортировка
    order_col = getattr(VMSnapshot, order_by, VMSnapshot.id)
    if desc:
        order_col = order_col.desc()
    stmt = stmt.order_by(order_col).offset(offset).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return rows, total

async def update_snapshot(
    db: AsyncSession,
    *,
    snapshot_id: int,
    name: Optional[str] = None,
) -> VMSnapshot:
    obj = await get_snapshot(db, snapshot_id=snapshot_id)

    changed = False
    if name is not None and name != obj.name:
        obj.name = name
        changed = True

    if not changed:
        return obj

    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise SnapshotAlreadyExists(
            f"Snapshot with name '{name}' already exists for VM {obj.vm_id}"
        ) from e

    await db.commit()
    await db.refresh(obj)
    return obj

async def delete_snapshot(db: AsyncSession, *, snapshot_id: int) -> None:
    obj = await get_snapshot(db, snapshot_id=snapshot_id)
    await db.delete(obj)
    await db.commit()
