from typing import List

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.v1.schemas.virtual_machine import (
    VMRead,
    VMCreate,
    VMUpdate,
)
from app.api.v1.dependencies import get_current_user, get_current_admin_user, AuthVerifyResponse
from app.api.v1.crud.virtual_machine import (
    get_vm,
    get_vms,
    create_vm,
    update_vm,
    delete_vm,
)
from app.api.v1.crud.ip_ranges import get_ip_range
from app.api.v1.crud.virtual_machine import get_vms_with_ip
from ipaddress import ip_address

router = APIRouter(
    prefix="/vms",
    tags=["Virtual Machine"],
)


@router.get("/", response_model=List[VMRead])
async def list_vms(
    db: AsyncSession = Depends(get_async_db),
    _: AuthVerifyResponse = Depends(get_current_user),
):
    return await get_vms(db)


@router.get("/{vm_id}", response_model=VMRead)
async def get_vm_details(
    vm_id: int,
    db: AsyncSession = Depends(get_async_db),
    _: AuthVerifyResponse = Depends(get_current_user),
):
    record = await get_vm(db, vm_id)
    if not record:
        raise HTTPException(status_code=404, detail="VM not found")
    return record


@router.post(
    "/",
    response_model=VMRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_new_vm(
    data: VMCreate,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    ip_range = await get_ip_range(db, data.ip_range_id)
    if not ip_range:
        raise HTTPException(status_code=400, detail="IP Range not found")

    # Собираем все уже занятые IP
    busy_vms = await get_vms_with_ip(db)
    busy_ips = {str(vm.ip_address) for vm in busy_vms if vm.ip_address}

    # Ищем первый свободный IP
    start = ip_address(ip_range.start_ip)
    end = ip_address(ip_range.end_ip)
    free_ip = None
    for ip_int in range(int(start), int(end) + 1):
        candidate = str(ip_address(ip_int))
        if candidate not in busy_ips:
            free_ip = candidate
            break

    if not free_ip:
        raise HTTPException(status_code=400, detail="No free IP in range")

    vm = await create_vm(db, data, free_ip, user.id)
    return vm


@router.put("/{vm_id}", response_model=VMRead)
async def update_vm_endpoint(
    vm_id: int,
    data: VMUpdate,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    vm = await get_vm(db, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    # Нельзя обновлять, если не владелец или не админ
    if not user.is_admin and vm.occupied_by != user.id:
        raise HTTPException(status_code=403, detail="Permission denied")

    updated = await update_vm(db, vm_id, data)
    return updated


@router.delete("/{vm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vm_endpoint(
    vm_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    vm = await get_vm(db, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    if not user.is_admin:
        if vm.status != "free":
            raise HTTPException(status_code=400, detail="Cannot delete non-free VM")
        if vm.occupied_by and vm.occupied_by != user.id:
            raise HTTPException(status_code=403, detail="Permission denied")

    success = await delete_vm(db, vm_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete VM")
