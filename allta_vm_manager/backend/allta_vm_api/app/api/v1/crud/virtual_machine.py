from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.models.virtual_machine import VirtualMachine
from app.api.v1.schemas.virtual_machine import VMCreate, VMUpdate


async def get_vm(db: AsyncSession, vm_id: int) -> Optional[VirtualMachine]:
    result = await db.execute(
        select(VirtualMachine).where(VirtualMachine.id == vm_id)
    )
    return result.scalar_one_or_none()


async def get_vms(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[VirtualMachine]:
    result = await db.execute(
        select(VirtualMachine).offset(skip).limit(limit)
    )
    return result.scalars().all()


async def get_vms_with_ip(db: AsyncSession) -> List[VirtualMachine]:
    """
    Вернуть все ВМ, у которых уже есть IP.
    """
    result = await db.execute(
        select(VirtualMachine).where(VirtualMachine.ip_address.isnot(None))
    )
    return result.scalars().all()


async def create_vm(db: AsyncSession, data: VMCreate) -> VirtualMachine:
    vm = VirtualMachine(**data.model_dump())
    db.add(vm)
    try:
        await db.commit()
        await db.refresh(vm)
    except IntegrityError as e:
        await db.rollback()
        raise ValueError(f"Failed to create VM: {str(e)}")
    return vm


async def update_vm(db: AsyncSession, vm_id: int, data: VMUpdate) -> Optional[VirtualMachine]:
    vm = await get_vm(db, vm_id)
    if not vm:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(vm, field, value)

    try:
        await db.commit()
        await db.refresh(vm)
    except IntegrityError as e:
        await db.rollback()
        raise ValueError(f"Failed to update VM: {str(e)}")
    return vm


async def delete_vm(db: AsyncSession, vm_id: int) -> bool:
    vm = await get_vm(db, vm_id)
    if not vm:
        return False

    await db.delete(vm)
    await db.commit()
    return True
