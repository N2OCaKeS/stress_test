# from typing import List, Optional
# from sqlalchemy import select
# from sqlalchemy.exc import IntegrityError
# from sqlalchemy.ext.asyncio import AsyncSession
# from typing import Iterable
# from app.api.v1.models.vm import VirtualMachine
# from app.api.v1.schemas.vm import (
#     VMCreate, VMUpdate, VMStatusUpdate, VMFixedStatus, normalize_status
# )

# async def get_vm(db: AsyncSession, vm_id: int) -> Optional[VirtualMachine]:
#     res = await db.execute(select(VirtualMachine).where(VirtualMachine.id == vm_id))
#     return res.scalar_one_or_none()

# async def get_vms(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[VirtualMachine]:
#     res = await db.execute(select(VirtualMachine).offset(skip).limit(limit))
#     return res.scalars().all()

# async def get_vms_by_ids(db: AsyncSession, ids: Iterable[int]) -> List[VirtualMachine]:
#     if not ids:
#         return []
#     res = await db.execute(select(VirtualMachine).where(VirtualMachine.id.in_(list(ids))))
#     return res.scalars().all()

# async def get_vms_by_names(db: AsyncSession, names: Iterable[str]) -> List[VirtualMachine]:
#     if not names:
#         return []
#     res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(list(names))))
#     return res.scalars().all()

# async def get_vms_with_ip(db: AsyncSession) -> List[VirtualMachine]:
#     res = await db.execute(select(VirtualMachine).where(VirtualMachine.ip_address.isnot(None)))
#     return res.scalars().all()

# async def create_vm(db: AsyncSession, data: VMCreate) -> VirtualMachine:
#     vm = VirtualMachine(**data.model_dump())
#     db.add(vm)
#     try:
#         await db.commit()
#         await db.refresh(vm)
#     except IntegrityError as e:
#         await db.rollback()
#         raise ValueError(f"Failed to create VM: {e.orig}")
#     return vm

# async def update_vm(db: AsyncSession, vm_id: int, data: VMUpdate) -> Optional[VirtualMachine]:
#     vm = await get_vm(db, vm_id)
#     if not vm:
#         return None
#     payload = data.model_dump(exclude_unset=True)
#     for k, v in payload.items():
#         setattr(vm, k, v)
#     try:
#         await db.commit()
#         await db.refresh(vm)
#     except IntegrityError as e:
#         await db.rollback()
#         raise ValueError(f"Failed to update VM: {e.orig}")
#     return vm

# async def delete_vm(db: AsyncSession, vm_id: int) -> bool:
#     vm = await get_vm(db, vm_id)
#     if not vm:
#         return False
#     await db.delete(vm)
#     await db.commit()
#     return True

# async def set_vm_status_raw(db: AsyncSession, vm_id: int, status: str) -> Optional[VirtualMachine]:
#     vm = await get_vm(db, vm_id)
#     if not vm:
#         return None
#     vm.status = normalize_status(status)
#     await db.commit()
#     await db.refresh(vm)
#     return vm

# async def release_vm(db: AsyncSession, vm_id: int, *, by_user: Optional[str] = None, force: bool = False) -> Optional[VirtualMachine]:
#     vm = await get_vm(db, vm_id)
#     if not vm:
#         return None
#     current = vm.status or VMFixedStatus.free.value
#     if current not in {s.value for s in VMFixedStatus}:
#         if not force and by_user and current != by_user:
#             raise ValueError(f"VM reserved by another user ({current}), cannot release")
#     vm.status = VMFixedStatus.free.value
#     await db.commit()
#     await db.refresh(vm)
#     return vm
