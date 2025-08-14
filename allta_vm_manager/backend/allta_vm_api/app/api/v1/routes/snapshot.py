from __future__ import annotations

from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.db.session import get_async_db
from app.api.v1.models.vm import VirtualMachine
from app.api.v1.models.vm_snapshot import VMSnapshot
from app.api.v1.schemas.vm_snapshot import VMSnapshotRead
from app.api.v1.dependencies import (
    get_current_user,        # возвращает объект с .login и .is_admin
    get_current_admin_user,  # валидирует, что это админ
)

router = APIRouter(prefix="/snapshots", tags=["Snapshots"])


# ---------------------- Schemas ----------------------

class SnapshotActionItem(BaseModel):
    vm_id: Optional[int] = Field(default=None, description="Либо vm_id...")
    vm_name: Optional[str] = Field(default=None, description="...либо vm_name")
    snapshot: str = Field(..., min_length=1, max_length=128)

    @model_validator(mode="after")
    def _one_identifier(self):
        if (self.vm_id is None and self.vm_name is None) or (
            self.vm_id is not None and self.vm_name is not None
        ):
            raise ValueError("Specify exactly one of vm_id or vm_name")
        return self


class SnapshotBatch(BaseModel):
    items: List[SnapshotActionItem]


# ---------------------- Helpers ----------------------

async def _resolve_vm(
    db: AsyncSession, *, vm_id: Optional[int], vm_name: Optional[str]
) -> VirtualMachine:
    """Ровно один идентификатор. Возвращает объект ВМ или 404."""
    if (vm_id is None and vm_name is None) or (vm_id is not None and vm_name is not None):
        raise HTTPException(status_code=422, detail="Specify exactly one of vm_id or vm_name")

    if vm_id is not None:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.id == vm_id))
        vm = res.scalar_one_or_none()
        if not vm:
            raise HTTPException(status_code=404, detail=f"VM with id={vm_id} not found")
        return vm

    # vm_name
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.name == vm_name))
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail=f"VM with name='{vm_name}' not found")
    if len(vms) > 1:
        raise HTTPException(status_code=409, detail=f"VM name '{vm_name}' is not unique; use vm_id")
    return vms[0]


def _can_user_touch_vm(vm: VirtualMachine, username: str, is_admin: bool) -> bool:
    """Админ всегда может. Пользователь — если VM свободна или занята им же."""
    if is_admin:
        return True
    status_val = (vm.status or "").strip()
    return status_val == "free" or status_val == username


async def _get_snapshot(db: AsyncSession, vm_id: int, name: str) -> Optional[VMSnapshot]:
    res = await db.execute(
        select(VMSnapshot).where(
            VMSnapshot.vm_id == vm_id,
            VMSnapshot.name == name,
        )
    )
    return res.scalar_one_or_none()


def _uuid_task() -> str:
    return str(uuid4())


# ---------------------- Endpoints ----------------------

@router.get("/", response_model=List[VMSnapshotRead])
async def list_snapshots(
    vm_id: Optional[int] = Query(default=None),
    vm_name: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Вернуть все снимки указанной ВМ (по id или name; ровно один параметр)."""
    vm = await _resolve_vm(db, vm_id=vm_id, vm_name=vm_name)
    res = await db.execute(
        select(VMSnapshot).where(VMSnapshot.vm_id == vm.id).order_by(VMSnapshot.id.desc())
    )
    return res.scalars().all()


@router.post("/create", status_code=status.HTTP_202_ACCEPTED)
async def create_snapshots(
    batch: SnapshotBatch,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """
    Создать снимки для списка ВМ: сначала записываем в БД, затем возвращаем task_id.
    Пользователь: разрешено если VM свободна или занята им; админ — всегда.
    """
    if not batch.items:
        raise HTTPException(status_code=400, detail="Empty items")

    # Собираем целевые ВМ и проверяем права/дубликаты
    targets: list[tuple[VirtualMachine, str]] = []
    for item in batch.items:
        vm = await _resolve_vm(db, vm_id=item.vm_id, vm_name=item.vm_name)
        if not _can_user_touch_vm(vm, user.login, user.is_admin):
            raise HTTPException(status_code=403, detail=f"Forbidden for VM {vm.id}")

        # мягкая предвалидация дубликата (может проиграть гонку — страхуемся через try/except ниже)
        exists = await _get_snapshot(db, vm.id, item.snapshot)
        if exists:
            raise HTTPException(
                status_code=409,
                detail=f"Snapshot '{item.snapshot}' already exists for VM id={vm.id}",
            )
        targets.append((vm, item.snapshot))

    # Пишем в БД одной транзакцией
    try:
        for vm, snap_name in targets:
            db.add(VMSnapshot(vm_id=vm.id, name=snap_name))
        await db.commit()
    except IntegrityError:
        # Возможно, за время проверки кто-то уже создал такой же снимок
        await db.rollback()
        conflicts = []
        for vm, snap_name in targets:
            if await _get_snapshot(db, vm.id, snap_name):
                conflicts.append(f"VM id={vm.id} already has snapshot '{snap_name}'")
        if conflicts:
            raise HTTPException(status_code=409, detail="; ".join(conflicts))
        # если причиной было что-то ещё
        raise HTTPException(status_code=500, detail="Failed to create snapshots")

    task_id = _uuid_task()
    # TODO: запустить реальную задачу создания снапшотов в гипервизоре
    return {"task_id": task_id}


@router.delete(
    "/delete",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(get_current_admin_user)],  # удалять снимки может только админ
)
async def delete_snapshots(
    batch: SnapshotBatch,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """
    Удаляем записи о снимках в БД, затем возвращаем task_id.
    """
    if not batch.items:
        raise HTTPException(status_code=400, detail="Empty items")

    # Находим все целевые снимки
    snaps_to_delete: list[VMSnapshot] = []
    missing: list[str] = []
    for item in batch.items:
        vm = await _resolve_vm(db, vm_id=item.vm_id, vm_name=item.vm_name)
        snap = await _get_snapshot(db, vm.id, item.snapshot)
        if not snap:
            missing.append(f"VM id={vm.id} has no snapshot '{item.snapshot}'")
        else:
            snaps_to_delete.append(snap)

    if missing:
        raise HTTPException(status_code=404, detail="; ".join(missing))

    # Удаляем одной транзакцией
    try:
        for snap in snaps_to_delete:
            await db.delete(snap)
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete snapshots")

    task_id = _uuid_task()
    # TODO: запустить реальную задачу удаления снапшотов в гипервизоре
    return {"task_id": task_id}


@router.post("/revert", status_code=status.HTTP_202_ACCEPTED)
async def revert_snapshots(
    batch: SnapshotBatch,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """
    Проверяем права и наличие снимков. В БД ничего не меняем, возвращаем task_id.
    """
    if not batch.items:
        raise HTTPException(status_code=400, detail="Empty items")

    errors: list[str] = []
    vm_ids: list[int] = []

    for item in batch.items:
        vm = await _resolve_vm(db, vm_id=item.vm_id, vm_name=item.vm_name)
        if not _can_user_touch_vm(vm, user.login, user.is_admin):
            errors.append(f"Forbidden for VM {vm.id}")
            continue
        snap = await _get_snapshot(db, vm.id, item.snapshot)
        if not snap:
            errors.append(f"VM id={vm.id} has no snapshot '{item.snapshot}'")
            continue
        vm_ids.append(vm.id)

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    task_id = _uuid_task()
    # TODO: запустить реальную задачу отката на гипервизоре
    return {"task_id": task_id}