from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.session import get_async_db
from app.api.v1.models.vm import VirtualMachine
from app.api.v1.schemas.vm import VMCreate, VMRead
from app.api.v1.schemas.vm_batch import (
    BatchVMCreateError,
    BatchVMCreateRequest,
    BatchVMCreateResponse,  # оставляем, если где-то используется
    BatchVMUpdateRequest,
    VMDeleteRequest,
    ItemOpError,
)
from app.utils.vm_batch import (
    ensure_server_ready_for_vms_hub,
    get_ip_range,
    get_range_bounds,
    ip_in_range,
    is_ip_free,
    is_name_free,
)
from app.api.v1.dependencies import (
    get_current_user,
    get_current_admin_user,
    get_token,
)
from app.api.v1.crud.vm import create_vm

router = APIRouter(prefix="/vm", tags=["VM"])

# -------- пресет базовых ВМ --------
PRESET_VMS: Dict[str, Dict[str, Any]] = {
    "virtual-station1": {"host-port": "22", "ip": "10.177.103.101", "ip_bridge": "10.177.103.101", "cpu": "16", "ram": "131072"},
    "virtual-station2": {"host-port": "22", "ip": "10.177.103.102", "ip_bridge": "10.177.103.102", "cpu": "16", "ram": "131072"},
    "virtual-station3": {"host-port": "22", "ip": "10.177.103.103", "ip_bridge": "10.177.103.103", "cpu": "16", "ram": "131072"},
    "virtual-station4": {"host-port": "22", "ip": "10.177.103.104", "ip_bridge": "10.177.103.104", "cpu": "16", "ram": "131072"},
    "work-station1":   {"host-port": "22", "ip": "10.177.103.201", "ip_bridge": "10.177.103.201", "cpu": "16", "ram": "131072"},
    "work-station2":   {"host-port": "22", "ip": "10.177.103.202", "ip_bridge": "10.177.103.202", "cpu": "16", "ram": "131072"},
}
PRESET_NAMES: Set[str] = set(PRESET_VMS.keys())


# -------- вспомогательные --------
def _can_modify_vm(vm: VirtualMachine, actor: str, is_admin: bool) -> Tuple[bool, Optional[str]]:
    """
    Разрешение на удаление/обновление/питание:
    - админ: всегда можно (включая базовые)
    - не админ:
        * если базовая (из пресета) — нельзя
        * если status == 'free' — можно
        * если status == actor (занял сам) — можно
        * иначе — нельзя
    """
    if is_admin:
        return True, None

    if vm.name in PRESET_NAMES:
        return False, "base vm: admin only"

    status_val = (vm.status or "").strip()
    if status_val == "free" or status_val == actor:
        return True, None
    return False, f"vm occupied by {status_val!r}"


def _uuid_task() -> str:
    return str(uuid.uuid4())


async def _create_vms_now(db: AsyncSession, items: List[VMCreate]) -> List[int]:
    """
    Синхронно создаёт ВМ в БД (через CRUD) и возвращает их id.
    Никакого Celery. Ошибки уникальности маппим в 409.
    """
    created_ids: List[int] = []
    for payload in items:
        try:
            vm = await create_vm(db, payload)  # внутри commit + refresh
            created_ids.append(vm.id)
        except ValueError as e:
            msg = str(e)
            # create_vm делает rollback на IntegrityError
            if "unique" in msg.lower() or "duplicate" in msg.lower():
                raise HTTPException(status_code=409, detail=f"Duplicate while creating VM {payload.name!r}: {msg}")
            raise HTTPException(status_code=400, detail=f"Failed to create VM {payload.name!r}: {msg}")
    return created_ids


# -------- endpoints --------

@router.post("/create", status_code=status.HTTP_202_ACCEPTED)
async def create(
    payload: BatchVMCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    token: str = Depends(get_token),
    user=Depends(get_current_user),  # любой авторизованный
):
    # сервер готов?
    ok, reason, _server = await ensure_server_ready_for_vms_hub(payload.server_id, token)
    if not ok:
        if reason and "not found" in reason:
            raise HTTPException(status_code=404, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "server not ready for vms hub")

    # диапазон существует?
    ipr = await get_ip_range(db, payload.ip_range_id)
    if not ipr:
        raise HTTPException(status_code=404, detail=f"IP range id={payload.ip_range_id} not found")
    start_ip, end_ip = get_range_bounds(ipr)

    # валидации и сбор payload'ов
    errs: List[BatchVMCreateError] = []
    to_create: List[VMCreate] = []
    for name, item in payload.vms.items():
        ip_str = str(item.ip)
        if not await is_name_free(db, name):
            errs.append(BatchVMCreateError(name=name, reason="name already exists"))
            continue
        if not ip_in_range(ip_str, start_ip, end_ip):
            errs.append(BatchVMCreateError(name=name, reason=f"ip {ip_str} is outside of range {start_ip}..{end_ip}"))
            continue
        if not await is_ip_free(db, ip_str):
            errs.append(BatchVMCreateError(name=name, reason=f"ip {ip_str} is already in use"))
            continue

        to_create.append(VMCreate(
            name=name,
            cpu=item.cpu,
            ram=item.ram,
            ip_address=ip_str,
            server_id=payload.server_id,
        ))

    if errs:
        # если нужно «создать то, что можно», можно убрать этот raise, вызвать _create_vms_now(to_create),
        # а в ответ вернуть и task_id, и skipped.
        raise HTTPException(status_code=400, detail={"skipped": [e.model_dump() for e in errs]})

    # создаём прямо сейчас (без Celery)
    await _create_vms_now(db, to_create)

    # возвращаем только task_id (контракт не меняем)
    task_id = _uuid_task()
    # TODO: позже тут можно запустить celery-задачу реального развёртывания
    return {"task_id": task_id}


@router.post(
    "/create-default-vms/{server_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Создать предзаданные ВМ на сервере (admin only)",
    dependencies=[Depends(get_current_admin_user)],
)
async def create_default_vms(
    server_id: int,
    db: AsyncSession = Depends(get_async_db),
    token: str = Depends(get_token),
    user=Depends(get_current_user),
):
    # сервер готов?
    ok, reason, _server = await ensure_server_ready_for_vms_hub(server_id, token)
    if not ok:
        if reason and "not found" in reason:
            raise HTTPException(status_code=404, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "server not ready for vms hub")

    # быстрый предчек имён/IP
    errs: List[BatchVMCreateError] = []
    to_create: List[VMCreate] = []
    for name, cfg in PRESET_VMS.items():
        ip = str(cfg["ip"])
        cpu = int(cfg["cpu"]) if isinstance(cfg["cpu"], str) else cfg["cpu"]
        ram = int(cfg["ram"]) if isinstance(cfg["ram"], str) else cfg["ram"]

        if not await is_name_free(db, name):
            errs.append(BatchVMCreateError(name=name, reason="name already exists"))
            continue
        if not await is_ip_free(db, ip):
            errs.append(BatchVMCreateError(name=name, reason=f"ip {ip} is already in use"))
            continue

        to_create.append(VMCreate(
            name=name,
            cpu=cpu,
            ram=ram,
            ip_address=ip,
            server_id=server_id,
        ))

    if errs:
        raise HTTPException(status_code=400, detail={"skipped": [e.model_dump() for e in errs]})

    # создаём прямо сейчас (без Celery)
    await _create_vms_now(db, to_create)

    task_id = _uuid_task()
    # TODO: позже тут можно запустить celery-задачу реального развёртывания
    return {"task_id": task_id}


@router.get("/{vm_id}", response_model=VMRead)
async def get_vm_by_id(
    vm_id: int,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.id == vm_id))
    vm = res.scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")
    return vm


@router.get("/", response_model=List[VMRead])
async def list_vms(
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, gt=0, le=1000),
):
    res = await db.execute(select(VirtualMachine).offset(skip).limit(limit))
    return res.scalars().all()


@router.delete("/", status_code=status.HTTP_202_ACCEPTED)
async def delete_vms(
    payload: VMDeleteRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    ids = payload.ids or []
    names = payload.names or []
    if not ids and not names:
        raise HTTPException(status_code=400, detail="Provide ids or names")

    where_clause = []
    if ids:
        where_clause.append(VirtualMachine.id.in_(ids))
    if names:
        where_clause.append(VirtualMachine.name.in_(names))

    res = await db.execute(
        select(VirtualMachine).where(*where_clause)
        if len(where_clause) == 1
        else select(VirtualMachine).where((where_clause[0]) | (where_clause[1]))
    )
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    denied: List[ItemOpError] = []
    allowed_ids: List[int] = []
    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.username, getattr(user, "is_admin", False))
        if ok:
            allowed_ids.append(vm.id)
        else:
            denied.append(ItemOpError(key=str(vm.id), reason=reason or "forbidden"))

    if denied:
        raise HTTPException(status_code=403, detail={"denied": [e.model_dump() for e in denied]})

    task_id = _uuid_task()
    # TODO: celery — удалить список ВМ
    return {"task_id": task_id}


@router.patch("/update", status_code=status.HTTP_202_ACCEPTED)
async def batch_update_vms(
    payload: BatchVMUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    if not payload.vms:
        raise HTTPException(status_code=400, detail="Empty payload")

    names = list(payload.vms.keys())
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    denied: List[ItemOpError] = []
    updates: Dict[int, Dict[str, int]] = {}
    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.username, getattr(user, "is_admin", False))
        if not ok:
            denied.append(ItemOpError(key=vm.name, reason=reason or "forbidden"))
            continue
        cfg = payload.vms.get(vm.name)
        if cfg:
            updates[vm.id] = {"cpu": cfg.cpu, "ram": cfg.ram}

    if denied:
        raise HTTPException(status_code=403, detail={"denied": [e.model_dump() for e in denied]})
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    task_id = _uuid_task()
    # TODO: celery — обновить cpu/ram у набора ВМ
    return {"task_id": task_id}


@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
async def power_start_vms(
    payload: VMDeleteRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    ids = payload.ids or []
    names = payload.names or []
    if not ids and not names:
        raise HTTPException(status_code=400, detail="Provide ids or names")

    clauses = []
    if ids:
        clauses.append(VirtualMachine.id.in_(ids))
    if names:
        clauses.append(VirtualMachine.name.in_(names))

    res = await db.execute(
        select(VirtualMachine).where(clauses[0]) if len(clauses) == 1
        else select(VirtualMachine).where(clauses[0] | clauses[1])
    )
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    allowed_ids: List[int] = []
    denied = []
    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.username, getattr(user, "is_admin", False))
        if ok:
            allowed_ids.append(vm.id)
        else:
            denied.append({"id": vm.id, "name": vm.name, "reason": reason or "forbidden"})

    if denied:
        raise HTTPException(status_code=403, detail={"denied": denied})

    task_id = _uuid_task()
    # TODO: celery — включить набор ВМ
    return {"task_id": task_id}


@router.post("/stop", status_code=status.HTTP_202_ACCEPTED)
async def power_stop_vms(
    payload: VMDeleteRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    ids = payload.ids or []
    names = payload.names or []
    if not ids and not names:
        raise HTTPException(status_code=400, detail="Provide ids or names")

    clauses = []
    if ids:
        clauses.append(VirtualMachine.id.in_(ids))
    if names:
        clauses.append(VirtualMachine.name.in_(names))

    res = await db.execute(
        select(VirtualMachine).where(clauses[0]) if len(clauses) == 1
        else select(VirtualMachine).where(clauses[0] | clauses[1])
    )
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    allowed_ids: List[int] = []
    denied = []
    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.username, getattr(user, "is_admin", False))
        if ok:
            allowed_ids.append(vm.id)
        else:
            denied.append({"id": vm.id, "name": vm.name, "reason": reason or "forbidden"})

    if denied:
        raise HTTPException(status_code=403, detail={"denied": denied})

    task_id = _uuid_task()
    # TODO: celery — выключить набор ВМ
    return {"task_id": task_id}
