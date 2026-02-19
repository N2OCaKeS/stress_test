from __future__ import annotations

from typing import Dict, List, Optional, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.v1.models.vm import VirtualMachine
from app.api.v1.models.vm_snapshot import VMSnapshot
from app.api.v1.schemas.vm_snapshot import (
    VMSnapshotRead,            # <-- добавили
    SnapshotSelection,
    SnapshotTaskEnvelope,
    SnapshotTaskOperation,
)
from app.api.v1.schemas.vm import ServerTaskInfo
from app.api.v1.dependencies import (
    AuthVerifyResponse,
    get_current_admin_user,
    get_token,
)
from app.utils.server_api import get_physical_server_from_remote
from app.utils.redis_queue import enqueue_task
from app.utils.config import settings

router = APIRouter(prefix="/snapshot", tags=["Snapshot"])


def _uuid_task() -> str:
    return str(uuid4())


def _can_manage_vms(user: AuthVerifyResponse) -> bool:
    return user.has_permission(settings.VM_MANAGE_PERMISSION)


def _server_task_info_from_api(srv, server_id: int) -> ServerTaskInfo:
    ip = str(getattr(srv, "ip_address", "") or getattr(srv, "admin_panel_ip", "")).strip()
    username = str(
        getattr(srv, "server_user", None) or getattr(srv, "admin_panel_user", None) or ""
    ).strip()
    password = str(
        getattr(srv, "server_password", None) or getattr(srv, "admin_panel_pass", None) or ""
    )
    phy_if_raw = str(getattr(srv, "phy_if", None) or getattr(srv, "phys_iface", None) or "").strip()

    if not ip:
        raise HTTPException(status_code=502, detail="Remote server has no IP field")
    if not username or not password:
        raise HTTPException(status_code=502, detail="Remote server has no credentials")

    return ServerTaskInfo(
        id=server_id,
        ip=ip,
        username=username,
        password=password,
        phy_if=phy_if_raw or None,
    )


def _vm_id(vm: VirtualMachine) -> int:
    return cast(int, vm.id)


def _vm_name(vm: VirtualMachine) -> str:
    return cast(str, vm.name)


def _vm_server_id(vm: VirtualMachine) -> int:
    return cast(int, vm.server_id)


def _ensure_same_server(vms: List[VirtualMachine]) -> int:
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs selected")
    sids = {_vm_server_id(vm) for vm in vms}
    if len(sids) != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Selected VMs belong to different servers: {sorted(sids)}",
        )
    return next(iter(sids))


def _can_user_touch_vm(vm: VirtualMachine, username: str, is_admin: bool) -> bool:
    if is_admin:
        return True
    st = (vm.status or "").strip()
    return st in ("free", username)


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
    return list(res.scalars().all())


async def _snapshot_exists(db: AsyncSession, vm_id: int, name: str) -> bool:
    res = await db.execute(
        select(VMSnapshot).where(VMSnapshot.vm_id == vm_id, VMSnapshot.name == name)
    )
    return res.scalar_one_or_none() is not None


@router.get(
    "/",
    summary="Список снимков для ВМ",
    response_model=List[VMSnapshotRead],   # <-- используем Pydantic-схему
)
async def list_snapshots(
    vm_id: Optional[int] = Query(default=None),
    vm_name: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    """
    Вернуть все снимки указанной ВМ (по id или name; ровно один параметр).
    """
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
        select(VMSnapshot).where(VMSnapshot.vm_id == _vm_id(vm)).order_by(VMSnapshot.id.desc())
    )
    snapshots = res.scalars().all()

    return snapshots


@router.post(
    "/create",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Создать снимок на наборе ВМ (ставит задачу воркеру)",
)
async def create_snapshots(
    sel: SnapshotSelection,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_admin_user),
    token: str = Depends(get_token),
):
    """
    Создать снимок `snapshot` для набора ВМ (ids/names).

    Валидации:
    - нет дублей ВМ в запросе (id + name на одну и ту же ВМ);
    - каждый VM доступен пользователю (админ — всегда; иначе VM free или занята им);
    - **имя снимка уникально для каждой ВМ** (предварительная проверка);
    - все ВМ на одном сервере.
    """
    ids = sel.ids or []
    names = sel.names or []
    snapshot_name = sel.snapshot

    # собрать ВМ
    vms_by_id: Dict[int, VirtualMachine] = {}
    if ids:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.id.in_(ids)))
        found = res.scalars().all()
        vms_by_id = {_vm_id(vm): vm for vm in found}
        miss = [vid for vid in ids if vid not in vms_by_id]
        if miss:
            raise HTTPException(status_code=404, detail={"missing_ids": miss})

    vms_by_name: Dict[str, VirtualMachine] = {}
    if names:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
        found = res.scalars().all()
        vms_by_name = {_vm_name(vm): vm for vm in found}
        miss = [nm for nm in names if nm not in vms_by_name]
        if miss:
            raise HTTPException(status_code=404, detail={"missing_names": miss})

    if not vms_by_id and not vms_by_name:
        raise HTTPException(status_code=404, detail="No VMs found by ids/names")

    # дубли ссылок
    refs: Dict[int, List[str]] = {}
    for vid in ids:
        if vid in vms_by_id:
            refs.setdefault(vid, []).append(f"id:{vid}")
    for nm in names:
        vm = vms_by_name.get(nm)
        if vm:
            refs.setdefault(_vm_id(vm), []).append(f"name:{nm}")
    dup = {k: v for k, v in refs.items() if len(v) > 1}
    if dup:
        raise HTTPException(
            status_code=400,
            detail={"error": "duplicate VM references in request",
                    "duplicates": [{"vm_id": k, "refs": v} for k, v in dup.items()]},
        )

    # итоговый набор
    unique: Dict[int, VirtualMachine] = {}
    unique.update(vms_by_id)
    for vm in vms_by_name.values():
        unique.setdefault(_vm_id(vm), vm)
    vms = list(unique.values())

    # права + уникальность имени снимка на каждой ВМ
    conflicts: List[int] = []
    for vm in vms:
        if not _can_user_touch_vm(vm, user.login, _can_manage_vms(user)):
            raise HTTPException(status_code=403, detail=f"Forbidden for VM id={_vm_id(vm)}")
        vm_id = _vm_id(vm)
        if await _snapshot_exists(db, vm_id, snapshot_name):
            conflicts.append(vm_id)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "snapshot already exists for some VMs",
                "snapshot": snapshot_name,
                "vm_ids": conflicts,
            },
        )

    # один сервер
    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    # задача
    task_id = _uuid_task()
    env = SnapshotTaskEnvelope(
        task_id=task_id,
        operation=SnapshotTaskOperation.create,
        server=_server_task_info_from_api(srv, server_id),
        vms={_vm_name(vm): _vm_id(vm) for vm in vms},
        snapshot_name=snapshot_name,
        json_remote_path=f"/opt/allta_vm/jobs/{task_id}.json",
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


@router.delete(
    "/delete",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Удалить снимок на наборе ВМ (ставит задачу воркеру)",
    dependencies=[Depends(get_current_admin_user)],
)
async def delete_snapshots(
    sel: SnapshotSelection,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
    token: str = Depends(get_token),
):
    """
    Удалить снимок `snapshot` на наборе ВМ (ids/names).

    Валидации:
    - нет дублей ВМ в запросе;
    - снимок должен существовать у **каждой** выбранной ВМ, иначе 404;
    - все ВМ — с одного сервера.
    """
    ids = sel.ids or []
    names = sel.names or []
    snapshot_name = sel.snapshot

    # собрать ВМ
    vms_by_id: Dict[int, VirtualMachine] = {}
    if ids:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.id.in_(ids)))
        found = res.scalars().all()
        vms_by_id = {_vm_id(vm): vm for vm in found}
        miss = [vid for vid in ids if vid not in vms_by_id]
        if miss:
            raise HTTPException(status_code=404, detail={"missing_ids": miss})

    vms_by_name: Dict[str, VirtualMachine] = {}
    if names:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
        found = res.scalars().all()
        vms_by_name = {_vm_name(vm): vm for vm in found}
        miss = [nm for nm in names if nm not in vms_by_name]
        if miss:
            raise HTTPException(status_code=404, detail={"missing_names": miss})

    if not vms_by_id and not vms_by_name:
        raise HTTPException(status_code=404, detail="No VMs found by ids/names")

    # дубли ссылок
    refs: Dict[int, List[str]] = {}
    for vid in ids:
        if vid in vms_by_id:
            refs.setdefault(vid, []).append(f"id:{vid}")
    for nm in names:
        vm = vms_by_name.get(nm)
        if vm:
            refs.setdefault(_vm_id(vm), []).append(f"name:{nm}")
    dup = {k: v for k, v in refs.items() if len(v) > 1}
    if dup:
        raise HTTPException(
            status_code=400,
            detail={"error": "duplicate VM references in request",
                    "duplicates": [{"vm_id": k, "refs": v} for k, v in dup.items()]},
        )

    # итоговый набор
    unique: Dict[int, VirtualMachine] = {}
    unique.update(vms_by_id)
    for vm in vms_by_name.values():
        unique.setdefault(_vm_id(vm), vm)
    vms = list(unique.values())

    # наличие снимка у каждой ВМ
    missing_on: List[int] = []
    for vm in vms:
        vm_id = _vm_id(vm)
        if not await _snapshot_exists(db, vm_id, snapshot_name):
            missing_on.append(vm_id)
    if missing_on:
        raise HTTPException(
            status_code=404,
            detail={"error": "snapshot not found for some VMs",
                    "snapshot": snapshot_name,
                    "vm_ids": missing_on},
        )

    # один сервер
    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    # задача
    task_id = _uuid_task()
    env = SnapshotTaskEnvelope(
        task_id=task_id,
        operation=SnapshotTaskOperation.delete,
        server=_server_task_info_from_api(srv, server_id),
        vms={_vm_name(vm): _vm_id(vm) for vm in vms},
        snapshot_name=snapshot_name,
        json_remote_path=f"/opt/allta_vm/jobs/{task_id}.json",
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


@router.post(
    "/revert",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Откат к снимку на наборе ВМ (ставит задачу воркеру)",
)
async def revert_snapshots(
    sel: SnapshotSelection,
    db: AsyncSession = Depends(get_async_db),
    user: AuthVerifyResponse = Depends(get_current_admin_user),
    token: str = Depends(get_token),
):
    """
    Откат к снимку `snapshot` на наборе ВМ (ids/names).

    Валидации:
    - нет дублей ВМ в запросе;
    - права (админ — всегда; иначе VM должна быть free или занята пользователем);
    - снимок должен существовать у каждой ВМ, иначе 404;
    - все ВМ — с одного сервера.
    """
    ids = sel.ids or []
    names = sel.names or []
    snapshot_name = sel.snapshot

    # собрать ВМ
    vms_by_id: Dict[int, VirtualMachine] = {}
    if ids:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.id.in_(ids)))
        found = res.scalars().all()
        vms_by_id = {_vm_id(vm): vm for vm in found}
        miss = [vid for vid in ids if vid not in vms_by_id]
        if miss:
            raise HTTPException(status_code=404, detail={"missing_ids": miss})

    vms_by_name: Dict[str, VirtualMachine] = {}
    if names:
        res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
        found = res.scalars().all()
        vms_by_name = {_vm_name(vm): vm for vm in found}
        miss = [nm for nm in names if nm not in vms_by_name]
        if miss:
            raise HTTPException(status_code=404, detail={"missing_names": miss})

    if not vms_by_id and not vms_by_name:
        raise HTTPException(status_code=404, detail="No VMs found by ids/names")

    # дубли ссылок
    refs: Dict[int, List[str]] = {}
    for vid in ids:
        if vid in vms_by_id:
            refs.setdefault(vid, []).append(f"id:{vid}")
    for nm in names:
        vm = vms_by_name.get(nm)
        if vm:
            refs.setdefault(_vm_id(vm), []).append(f"name:{nm}")
    dup = {k: v for k, v in refs.items() if len(v) > 1}
    if dup:
        raise HTTPException(
            status_code=400,
            detail={"error": "duplicate VM references in request",
                    "duplicates": [{"vm_id": k, "refs": v} for k, v in dup.items()]},
        )

    # итоговый набор
    unique: Dict[int, VirtualMachine] = {}
    unique.update(vms_by_id)
    for vm in vms_by_name.values():
        unique.setdefault(_vm_id(vm), vm)
    vms = list(unique.values())

    # права + наличие снимка
    missing_on: List[int] = []
    for vm in vms:
        if not _can_user_touch_vm(vm, user.login, _can_manage_vms(user)):
            raise HTTPException(status_code=403, detail=f"Forbidden for VM id={_vm_id(vm)}")
        vm_id = _vm_id(vm)
        if not await _snapshot_exists(db, vm_id, snapshot_name):
            missing_on.append(vm_id)
    if missing_on:
        raise HTTPException(
            status_code=404,
            detail={"error": "snapshot not found for some VMs",
                    "snapshot": snapshot_name,
                    "vm_ids": missing_on},
        )

    # один сервер
    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    # задача
    task_id = _uuid_task()
    env = SnapshotTaskEnvelope(
        task_id=task_id,
        operation=SnapshotTaskOperation.revert,
        server=_server_task_info_from_api(srv, server_id),
        vms={_vm_name(vm): _vm_id(vm) for vm in vms},
        snapshot_name=snapshot_name,
        json_remote_path=f"/opt/allta_vm/jobs/{task_id}.json",
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}
