from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.api.v1.schemas.vm_snapshot import (
    VMSnapshotCreate,
    VMSnapshotRead,
    VMSnapshotUpdate,
)

from app.api.v1.dependencies import get_current_user, AuthVerifyResponse
from app.db.session import get_async_db

from app.api.v1.crud.vm_snapshot import (
    create_snapshot,
    get_snapshots,
    get_snapshot,
    delete_snapshot,
    update_snapshot,
)
from app.api.v1.crud.virtual_machine import get_vm

router = APIRouter(
    prefix="/snapshot",
    tags=["VM Snapshot"],
)


@router.post(
    "/",
    response_model=VMSnapshotRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать снимок ВМ"
)
async def create_snapshot(
    data: VMSnapshotCreate,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    vm = await get_vm(db, data.vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    # проверка прав
    if not user.is_admin and (vm.occupied_by != user.id):
        raise HTTPException(status_code=403, detail="Permission denied")

    snapshot = await create_snapshot(db, data)
    return snapshot


@router.get(
    "/vm/{vm_id}",
    response_model=List[VMSnapshotRead],
    summary="Список снимков ВМ"
)
async def list_snapshots(
    vm_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    vm = await get_vm(db, vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    snapshots = await get_snapshots(db, vm_id)
    return snapshots


@router.delete(
    "/{snapshot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить снимок"
)
async def delete_snapshot(
    snapshot_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    snapshot = await get_snapshot(db, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    vm = await get_vm(db, snapshot.vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    if not user.is_admin and (vm.occupied_by != user.id):
        raise HTTPException(status_code=403, detail="Permission denied")

    success = await delete_snapshot(db, snapshot_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete snapshot")


@router.patch(
    "/{snapshot_id}",
    response_model=VMSnapshotRead,
    summary="Обновить описание снимка"
)
async def update_snapshot(
    snapshot_id: int,
    data: VMSnapshotUpdate,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    snapshot = await get_snapshot(db, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    vm = await get_vm(db, snapshot.vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    if not user.is_admin and (vm.occupied_by != user.id):
        raise HTTPException(status_code=403, detail="Permission denied")

    updated = await update_snapshot(db, snapshot_id, data)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update snapshot")

    return updated

@router.post(
    "/{snapshot_id}/rollback",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Откат ВМ к снимку (асинхронная задача)"
)
async def rollback_snapshot(
    snapshot_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    snapshot = await get_snapshot(db, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    vm = await get_vm(db, snapshot.vm_id)
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    if not user.is_admin and (vm.occupied_by != user.id):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Заглушка
    return {
        "message": "Rollback task has been accepted (stub).",
        "snapshot_id": snapshot_id,
        "vm_id": vm.id,
        "user_id": user.id
    }
