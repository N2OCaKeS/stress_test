from typing import List, Optional, Iterable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.models.vm import VirtualMachine

async def get_vm(db: AsyncSession, vm_id: int) -> Optional[VirtualMachine]:
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.id == vm_id))
    return res.scalar_one_or_none()

async def get_vms(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[VirtualMachine]:
    res = await db.execute(select(VirtualMachine).offset(skip).limit(limit))
    return res.scalars().all()

async def get_vms_by_ids(db: AsyncSession, ids: Iterable[int]) -> List[VirtualMachine]:
    ids = list(ids or [])
    if not ids:
        return []
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.id.in_(ids)))
    return res.scalars().all()

async def get_vms_by_names(db: AsyncSession, names: Iterable[str]) -> List[VirtualMachine]:
    names = list(names or [])
    if not names:
        return []
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
    return res.scalars().all()

async def get_vms_with_ip(db: AsyncSession) -> List[VirtualMachine]:
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.ip_address.isnot(None)))
    return res.scalars().all()
