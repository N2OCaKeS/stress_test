from __future__ import annotations

from typing import List, Optional, Tuple, Set
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.session import get_async_db
from app.api.v1.models.vm import VirtualMachine
from app.api.v1.models.vm_snapshot import VMSnapshot
from app.api.v1.schemas.vm_snapshot import VMSnapshotRead
from app.api.v1.dependencies import (
    get_current_user,
    get_current_admin_user,
    get_token,
)
from app.utils.server_api import get_physical_server_from_remote
from app.utils.redis_queue import enqueue_task

router = APIRouter(prefix="/snapshot", tags=["Snapshot"])


class SnapshotSelection(BaseModel):
    ids: Optional[List[int]] = Field(default=None, description="Список VM id")
    names: Optional[List[str]] = Field(default=None, description="Список VM name")
    snapshot: str = Field(..., min_length=1, max_length=128)

    def ensure_non_empty(self):
        if not self.ids and not self.names:
            raise HTTPException(status_code=400, detail="Provide ids or names")



def _uuid_task() -> str:
    return str(uuid4())

def _server_task_info_from_api(srv) -> dict:
    return {
        "ip": str(getattr(srv, "ip_address", "")),
        "username": getattr(srv, "server_user", None),
        "password": getattr(srv, "server_password", None),
        "phy_if": getattr(srv, "phy_if", None),
    }

def _ensure_same_server(vms: List[VirtualMachine]) -> int:
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs selected")
    sids = {vm.server_id for vm in vms}
    if len(sids) != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Selected VMs belong to different servers: {sorted(sids)}",
        )
    return next(iter(sids))

def _can_user_touch_vm(vm: VirtualMachine, username: str, is_admin: bool) -> bool:
    """Для create/revert: админ всегда; иначе VM свободна или занята этим пользователем."""
    if is_admin:
        return True
    st = (vm.status or "").strip()
    return st == "free" or st == username

async def _resolve_vms_by_ids_names(
    db: AsyncSession, *, ids: Optional[List[int]], names: Optional[List[str]]
) -> List[VirtualMachine]:
    clauses = []
    if ids:
        clauses.append(VirtualMachine.id.in_(ids))
    if names:
        clauses.append(VirtualMachine.name.in_(names))
    if not clauses:
        return []
    if len(clauses) == 1:
        res = await db.execute(select(VirtualMachine).where(clauses[0]))
    else:
        res = await db.execute(select(VirtualMachine).where(clauses[0] | clauses[1]))
    return res.scalars().all()

async def _get_snapshot(db: AsyncSession, vm_id: int, name: str) -> Optional[VMSnapshot]:
    res = await db.execute(
        select(VMSnapshot).where(VMSnapshot.vm_id == vm_id, VMSnapshot.name == name)
    )
    return res.scalar_one_or_none()

def _envelope(
    *, task_id: str, operation: str, server: dict,
    vm_names: Optional[List[str]] = None, snapshot_name: Optional[str] = None
) -> dict:
    return {
        "task_id": task_id,
        "operation": operation,
        "server": server,
        "vms_full": None,
        "vm_names": vm_names,
        "snapshot_name": snapshot_name,
        "rc": None,
        "box": None,
        "kernel": None,
        "json_remote_path": f"/opt/allta_vm/jobs/{task_id}.json",
    }


@router.get("/", response_model=List[VMSnapshotRead])
async def list_snapshots(
    vm_id: Optional[int] = Query(default=None),
    vm_name: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """Вернуть все снимки указанной ВМ (по id или name; ровно один параметр)."""
    if (vm_id is None) == (vm_name is None):
        raise HTTPException(status_code=422, detail="Specify exactly one of vm_id or vm_name")
    if vm_id is not None:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.id == vm_id))
        vm = res.scalar_one_or_none()
        if not vm:
            raise HTTPException(status_code=404, detail=f"VM id={vm_id} not found")
    else:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.name == vm_name))
        vm = res.scalar_one_or_none()
        if not vm:
            raise HTTPException(status_code=404, detail=f"VM name='{vm_name}' not found")
    res = await db.execute(
        select(VMSnapshot).where(VMSnapshot.vm_id == vm.id).order_by(VMSnapshot.id.desc())
    )
    return res.scalars().all()

@router.post("/create", status_code=status.HTTP_202_ACCEPTED)
async def create_snapshots(
    sel: SnapshotSelection,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    """
    Создать снимок `snapshot` для набора ВМ (ids/names).
    Правила:
      - авторизованные: VM свободна или занята этим пользователем;
      - админ — всегда;
      - если снимок уже есть хотя бы у одной ВМ → 409;
      - все ВМ должны быть на одном сервере.
    """
    sel.ensure_non_empty()
    snapshot_name = sel.snapshot

    vms = await _resolve_vms_by_ids_names(db, ids=sel.ids, names=sel.names)
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found by ids/names")

    # права + предвалидация отсутствия снимка
    for vm in vms:
        if not _can_user_touch_vm(vm, user.login, getattr(user, "is_admin", False)):
            raise HTTPException(status_code=403, detail=f"Forbidden for VM id={vm.id}")
        if await _get_snapshot(db, vm.id, snapshot_name):
            raise HTTPException(
                status_code=409,
                detail=f"Snapshot '{snapshot_name}' already exists for VM id={vm.id}",
            )

    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    try:
        for vm in vms:
            db.add(VMSnapshot(vm_id=vm.id, name=snapshot_name))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        conflicted = [vm.id for vm in vms if (await _get_snapshot(db, vm.id, snapshot_name))]
        if conflicted:
            raise HTTPException(status_code=409, detail=f"Snapshot already exists for VMs: {conflicted}")
        raise HTTPException(status_code=500, detail="Failed to create snapshots")

    task_id = _uuid_task()
    vm_names = [vm.name for vm in vms]
    env = _envelope(
        task_id=task_id,
        operation="snapshot.create",
        server=_server_task_info_from_api(srv),
        vm_names=vm_names,
        snapshot_name=snapshot_name,
    )
    await enqueue_task(env)

    return {"task_id": task_id}

@router.delete(
    "/delete",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(get_current_admin_user)],
)
async def delete_snapshots(
    sel: SnapshotSelection,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    """
    Удалить снимок `snapshot` на наборе ВМ (ids/names).
    Требования:
      - только админ;
      - снимок должен существовать на каждой ВМ;
      - все ВМ — с одного сервера.
    """
    sel.ensure_non_empty()
    snapshot_name = sel.snapshot

    vms = await _resolve_vms_by_ids_names(db, ids=sel.ids, names=sel.names)
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found by ids/names")

    snaps_to_delete: List[VMSnapshot] = []
    missing: List[int] = []
    for vm in vms:
        snap = await _get_snapshot(db, vm.id, snapshot_name)
        if not snap:
            missing.append(vm.id)
        else:
            snaps_to_delete.append(snap)
    if missing:
        raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_name}' not found for VMs: {missing}")

    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    try:
        for snap in snaps_to_delete:
            await db.delete(snap)
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete snapshots")

    task_id = _uuid_task()
    vm_names = [vm.name for vm in vms]
    env = _envelope(
        task_id=task_id,
        operation="snapshot.delete",
        server=_server_task_info_from_api(srv),
        vm_names=vm_names,
        snapshot_name=snapshot_name,
    )
    await enqueue_task(env)

    return {"task_id": task_id}

@router.post("/revert", status_code=status.HTTP_202_ACCEPTED)
async def revert_snapshots(
    sel: SnapshotSelection,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    """
    Откат к снимку `snapshot` на наборе ВМ (ids/names).
    Правила:
      - авторизованные: VM свободна или занята этим пользователем;
      - админ — всегда;
      - снимок должен существовать на каждой ВМ;
      - все ВМ — с одного сервера.
    """
    sel.ensure_non_empty()
    snapshot_name = sel.snapshot

    vms = await _resolve_vms_by_ids_names(db, ids=sel.ids, names=sel.names)
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found by ids/names")

    missing: List[int] = []
    for vm in vms:
        if not _can_user_touch_vm(vm, user.login, getattr(user, "is_admin", False)):
            raise HTTPException(status_code=403, detail=f"Forbidden for VM id={vm.id}")
        if not await _get_snapshot(db, vm.id, snapshot_name):
            missing.append(vm.id)
    if missing:
        raise HTTPException(status_code=400, detail=f"Snapshot '{snapshot_name}' not found for VMs: {missing}")

    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    task_id = _uuid_task()
    vm_names = [vm.name for vm in vms]
    env = _envelope(
        task_id=task_id,
        operation="snapshot.revert",
        server=_server_task_info_from_api(srv),
        vm_names=vm_names,
        snapshot_name=snapshot_name,
    )
    await enqueue_task(env)

    return {"task_id": task_id}
