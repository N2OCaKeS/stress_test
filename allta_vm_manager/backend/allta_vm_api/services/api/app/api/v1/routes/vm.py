from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.v1.models.vm import VirtualMachine
from app.api.v1.schemas.vm import VMCreate, VMRead, VMUpdate
from app.api.v1.schemas.vm_batch import (
    BatchVMCreateError,
    BatchVMCreateRequest,
    BatchVMUpdateRequest,
    VMDeleteRequest,
    ItemOpError,
    AstraUpdateRequest,
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
from app.api.v1.crud.vm_snapshot import create_snapshot, SnapshotAlreadyExists
from app.api.v1.crud.vm import create_vm, delete_vm, update_vm
from app.api.v1.crud.vm_snapshot import list_snapshots, delete_snapshot
from app.utils.server_api import get_physical_server_from_remote, get_os_versions
from app.utils.redis_queue import enqueue_task
from app.utils.config import settings

router = APIRouter(prefix="/vm", tags=["VM"])

PRESET_VMS: Dict[str, Dict[str, Any]] = {
    "virtual-station1": {"host-port": "22", "ip": "10.177.103.101", "ip_bridge": "10.177.103.101", "cpu": "16", "ram": "131072"},
    "virtual-station2": {"host-port": "22", "ip": "10.177.103.102", "ip_bridge": "10.177.103.102", "cpu": "16", "ram": "131072"},
    "virtual-station3": {"host-port": "22", "ip": "10.177.103.103", "ip_bridge": "10.177.103.103", "cpu": "16", "ram": "131072"},
    "virtual-station4": {"host-port": "22", "ip": "10.177.103.104", "ip_bridge": "10.177.103.104", "cpu": "16", "ram": "131072"},
    "work-station1": {"host-port": "22", "ip": "10.177.103.201", "ip_bridge": "10.177.103.201", "cpu": "16", "ram": "131072"},
    "work-station2": {"host-port": "22", "ip": "10.177.103.202", "ip_bridge": "10.177.103.202", "cpu": "16", "ram": "131072"},
}
PRESET_NAMES: Set[str] = set(PRESET_VMS.keys())
DEFAULT_SNAPSHOTS = ["1.8.1.6", "1.7.5.9"]


def _uuid_task() -> str:
    """Сгенерировать уникальный идентификатор задачи."""
    return str(uuid.uuid4())


def _can_modify_vm(vm: VirtualMachine, actor: str, is_admin: bool) -> Tuple[bool, Optional[str]]:
    """Проверить право изменения ВМ: админ — всегда; пользователь — если ВМ свободна или занята им; базовые ВМ только для админа."""
    if is_admin:
        return True, None
    if vm.name in PRESET_NAMES:
        return False, "base vm: admin only"
    status_val = (vm.status or "").strip()
    if status_val == "free" or status_val == actor:
        return True, None
    return False, f"vm occupied by {status_val!r}"


def _server_task_info_from_api(srv) -> Dict[str, Any]:
    """Привести информацию о сервере к JSON‑совместимому виду для постановки задачи."""
    return {
        "ip": str(getattr(srv, "ip_address", "")),
        "username": getattr(srv, "server_user", None),
        "password": getattr(srv, "server_password", None),
        "phy_if": getattr(srv, "phy_if", None),
    }


def _ensure_same_server(vms: List[VirtualMachine]) -> int:
    """Убедиться, что все ВМ относятся к одному серверу, и вернуть его идентификатор."""
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")
    server_ids = {vm.server_id for vm in vms}
    if len(server_ids) != 1:
        raise HTTPException(status_code=400, detail=f"Selected VMs belong to different servers: {sorted(server_ids)}")
    return next(iter(server_ids))


def _envelope(
    *,
    task_id: str,
    operation: str,
    server: Dict[str, Any],
    new_password: str = settings.VM_PASS,    
    vms_full: Optional[Dict[str, Dict[str, Any]]] = None,
    vm_names: Optional[List[str]] = None,
    snapshot_name: Optional[str] = None,
    rc: Optional[str] = None,
    box: Optional[str] = None,
    kernel: Optional[str] = None

) -> Dict[str, Any]:
    """Сформировать полезную нагрузку для очереди: операция, сервер, конфигурация ВМ, имена ВМ, RC/снимок/бокс."""
    return {
        "task_id": task_id,
        "operation": operation,
        "server": server,
        "new_password": new_password,        
        "vms_full": vms_full,
        "vm_names": vm_names,
        "snapshot_name": snapshot_name,
        "rc": rc,
        "box": box,
        "kernel": kernel,
        "json_remote_path": f"/opt/allta_vm/jobs/{task_id}.json",
    }


async def _create_vms_now(db: AsyncSession, items: List[VMCreate]) -> List[VirtualMachine]:
    """Создать набор ВМ в базе и вернуть ORM‑объекты с присвоенными id."""
    created: List[VirtualMachine] = []
    for payload in items:
        try:
            vm = await create_vm(db, payload)
            created.append(vm)
        except ValueError as e:
            msg = str(e)
            if "unique" in msg.lower() or "duplicate" in msg.lower():
                raise HTTPException(status_code=409, detail=f"Duplicate while creating VM {payload.name!r}: {msg}")
            raise HTTPException(status_code=400, detail=f"Failed to create VM {payload.name!r}: {msg}")
    return created


@router.post("/create", status_code=status.HTTP_202_ACCEPTED,)
async def create(
    payload: BatchVMCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    token: str = Depends(get_token),
    user=Depends(get_current_user),
):
    """
    Создать набор ВМ.

    Доступ: авторизованные пользователи.
    Валидация: проверяется готовность сервера, принадлежность IP диапазону, уникальность имён и IP.
    Побочные эффекты: записи ВМ сохраняются в БД; в очередь отправляется задача `vm.create` с полной конфигурацией,
    дополнительно регистрируются записи снимков в БД.
    Ответ: идентификатор задачи.
    Коды ошибок: 400, 404, 409.
    """
    ok, reason, _ = await ensure_server_ready_for_vms_hub(payload.server_id, token)
    if not ok:
        if reason and "not found" in reason:
            raise HTTPException(status_code=404, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "server not ready for vms hub")

    ipr = await get_ip_range(db, payload.ip_range_id)
    if not ipr:
        raise HTTPException(status_code=404, detail=f"IP range id={payload.ip_range_id} not found")
    start_ip, end_ip = get_range_bounds(ipr)

    errs: List[BatchVMCreateError] = []
    to_create_payloads: List[VMCreate] = []
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

        to_create_payloads.append(
            VMCreate(
                name=name,
                cpu=item.cpu,
                ram=item.ram,
                ip_address=ip_str,
                server_id=payload.server_id,
            )
        )

    if errs:
        raise HTTPException(status_code=400, detail={"skipped": [e.model_dump() for e in errs]})

    created_vms = await _create_vms_now(db, to_create_payloads)

    srv = await get_physical_server_from_remote(payload.server_id, token)
    task_id = _uuid_task()
    vms_full = {
        vm.name: {
            "cpu": vm.cpu,
            "ram": vm.ram,
            "host-port": "22",
            "ip_bridge": str(vm.ip_address),
        }
        for vm in created_vms
    }
    box = "vm_station"
    env = _envelope(
        task_id=task_id,
        operation="vm.create",
        server=_server_task_info_from_api(srv),
        vms_full=vms_full,
        box=box,
    )
    await enqueue_task(env)

    if box:
        snapshots_to_create: List[str] = []
        if box == "vm_station":
            snapshots_to_create = ["1.8.1.6", "1.7.5.9"]
        elif box == "vm_station1.7" or str(box).startswith("1.7"):
            snapshots_to_create = ["1.7.5.9"]
        elif box == "vm_station1.8" or str(box).startswith("1.8"):
            snapshots_to_create = ["1.8.1.6"]
        for vm in created_vms:
            for snap_name in snapshots_to_create:
                try:
                    await create_snapshot(db, vm_id=vm.id, name=snap_name)
                except SnapshotAlreadyExists:
                    pass
    else:
        for vm in created_vms:
            for snap_name in DEFAULT_SNAPSHOTS:
                try:
                    await create_snapshot(db, vm_id=vm.id, name=snap_name)
                except SnapshotAlreadyExists:
                    pass

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
    """
    Создать предзаданный набор ВМ на указанном сервере.

    Доступ: только администраторы.
    Побочные эффекты: записи ВМ сохраняются в БД; в очередь отправляется задача `vm.base_create`;
    для каждой ВМ создаются записи двух дефолтных снимков.
    Ответ: идентификатор задачи.
    Коды ошибок: 400, 404, 409.
    """
    ok, reason, _ = await ensure_server_ready_for_vms_hub(server_id, token)
    if not ok:
        if reason and "not found" in reason:
            raise HTTPException(status_code=404, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "server not ready for vms hub")

    errs: List[BatchVMCreateError] = []
    to_create_payloads: List[VMCreate] = []
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
        to_create_payloads.append(VMCreate(name=name, cpu=cpu, ram=ram, ip_address=ip, server_id=server_id))

    if errs:
        raise HTTPException(status_code=400, detail={"skipped": [e.model_dump() for e in errs]})

    created_vms = await _create_vms_now(db, to_create_payloads)

    srv = await get_physical_server_from_remote(server_id, token)
    task_id = _uuid_task()
    vms_full = {
        vm.name: {
            "cpu": vm.cpu,
            "ram": vm.ram,
            "host-port": "22",
            "ip_bridge": str(vm.ip_address),
        }
        for vm in created_vms
    }
    env = _envelope(
        task_id=task_id,
        operation="vm.base_create",
        server=_server_task_info_from_api(srv),
        vms_full=vms_full,
    )
    await enqueue_task(env)

    for vm in created_vms:
        for snap_name in DEFAULT_SNAPSHOTS:
            try:
                await create_snapshot(db, vm_id=vm.id, name=snap_name)
            except SnapshotAlreadyExists:
                pass

    return {"task_id": task_id}


@router.get("/{vm_id}", response_model=VMRead)
async def get_vm_by_id(
    vm_id: int,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
):
    """
    Получить информацию о ВМ по её идентификатору.

    Доступ: авторизованные пользователи.
    Параметры: vm_id — идентификатор ВМ.
    Ответ: объект VMRead.
    Коды ошибок: 404.
    """
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
    """
    Список ВМ с пагинацией.

    Доступ: авторизованные пользователи.
    Параметры: skip, limit.
    Ответ: массив VMRead.
    """
    res = await db.execute(select(VirtualMachine).offset(skip).limit(limit))
    return res.scalars().all()


@router.delete("/", status_code=status.HTTP_202_ACCEPTED)
async def delete_vms(
    payload: VMDeleteRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    """
    Удалить одну или несколько ВМ по id или имени.

    Доступ: авторизованные пользователи с правом на конкретные ВМ; базовые ВМ — только админ.
    Поведение: ставится задача `vm.delete`; после постановки записи ВМ и их снимков удаляются из БД.
    Ответ: идентификатор задачи.
    Коды ошибок: 400, 403, 404.
    """
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
        else select(VirtualMachine).where((clauses[0]) | (clauses[1]))
    )
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    denied: List[ItemOpError] = []
    vm_names: List[str] = []
    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.login, getattr(user, "is_admin", False))
        if ok:
            vm_names.append(vm.name)
        else:
            denied.append(ItemOpError(key=str(vm.id), reason=reason or "forbidden"))
    if denied:
        raise HTTPException(status_code=403, detail={"denied": [e.model_dump() for e in denied]})

    task_id = _uuid_task()
    env = _envelope(
        task_id=task_id,
        operation="vm.delete",
        server=_server_task_info_from_api(srv),
        vm_names=vm_names,
    )
    await enqueue_task(env)

    for vm in vms:
        snaps, _ = await list_snapshots(db, vm_id=vm.id)
        for snap in snaps:
            await delete_snapshot(db, snapshot_id=snap.id)
        await delete_vm(db, vm_id=vm.id)

    return {"task_id": task_id}


@router.patch("/update", status_code=status.HTTP_202_ACCEPTED)
async def batch_update_vms(
    payload: BatchVMUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    """
    Массово обновить параметры ВМ (cpu/ram) по именам.

    Доступ: авторизованные пользователи с правом на конкретные ВМ; базовые ВМ — только админ.
    Поведение: изменения сразу записываются в БД; дополнительно ставится задача `vm.update` с патчами.
    Ответ: идентификатор задачи.
    Коды ошибок: 400, 403, 404, 409.
    """
    if not payload.vms:
        raise HTTPException(status_code=400, detail="Empty payload")

    names = list(payload.vms.keys())
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.name.in_(names)))
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    found_names = {vm.name for vm in vms}
    missing = [nm for nm in names if nm not in found_names]
    if missing:
        raise HTTPException(status_code=404, detail={"missing_names": missing})

    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    denied = []
    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.login, getattr(user, "is_admin", False))
        if not ok:
            denied.append({"name": vm.name, "reason": reason or "forbidden"})
    if denied:
        raise HTTPException(status_code=403, detail={"denied": denied})

    vms_full: Dict[str, Dict[str, int]] = {}
    for vm in vms:
        cfg = payload.vms.get(vm.name)
        patch_data: Dict[str, int] = {}
        if cfg and getattr(cfg, "cpu", None) is not None:
            patch_data["cpu"] = cfg.cpu
        if cfg and getattr(cfg, "ram", None) is not None:
            patch_data["ram"] = cfg.ram

        if patch_data:
            try:
                await update_vm(db, vm.id, VMUpdate(**patch_data))
            except ValueError as e:
                msg = str(e)
                if "unique" in msg.lower() or "duplicate" in msg.lower():
                    raise HTTPException(status_code=409, detail=msg)
                raise HTTPException(status_code=400, detail=msg)
            vms_full[vm.name] = patch_data

    if not vms_full:
        raise HTTPException(status_code=400, detail="Nothing to update")

    task_id = _uuid_task()
    env = _envelope(
        task_id=task_id,
        operation="vm.update",
        server=_server_task_info_from_api(srv),
        vms_full=vms_full,
    )
    await enqueue_task(env)
    return {"task_id": task_id}


@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
async def power_start_vms(
    payload: VMDeleteRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    """
    Включить одну или несколько ВМ по id или имени.

    Доступ: авторизованные пользователи с правом на конкретные ВМ; базовые ВМ — только админ.
    Поведение: ставится задача `vm.start`.
    Ответ: идентификатор задачи.
    Коды ошибок: 400, 403, 404.
    """
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
        else select(VirtualMachine).where((clauses[0]) | (clauses[1]))
    )
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    denied = []
    vm_names: List[str] = []
    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.login, getattr(user, "is_admin", False))
        if ok:
            vm_names.append(vm.name)
        else:
            denied.append({"id": vm.id, "name": vm.name, "reason": reason or "forbidden"})
    if denied:
        raise HTTPException(status_code=403, detail={"denied": denied})

    task_id = _uuid_task()
    env = _envelope(
        task_id=task_id,
        operation="vm.start",
        server=_server_task_info_from_api(srv),
        vm_names=vm_names,
    )
    await enqueue_task(env)
    return {"task_id": task_id}


@router.post("/stop", status_code=status.HTTP_202_ACCEPTED)
async def power_stop_vms(
    payload: VMDeleteRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    """
    Выключить одну или несколько ВМ по id или имени.

    Доступ: авторизованные пользователи с правом на конкретные ВМ; базовые ВМ — только админ.
    Поведение: ставится задача `vm.stop`.
    Ответ: идентификатор задачи.
    Коды ошибок: 400, 403, 404.
    """
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
        else select(VirtualMachine).where((clauses[0]) | (clauses[1]))
    )
    vms = res.scalars().all()
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    server_id = _ensure_same_server(vms)
    srv = await get_physical_server_from_remote(server_id, token)

    denied = []
    vm_names: List[str] = []
    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.login, getattr(user, "is_admin", False))
        if ok:
            vm_names.append(vm.name)
        else:
            denied.append({"id": vm.id, "name": vm.name, "reason": reason or "forbidden"})
    if denied:
        raise HTTPException(status_code=403, detail={"denied": denied})

    task_id = _uuid_task()
    env = _envelope(
        task_id=task_id,
        operation="vm.stop",
        server=_server_task_info_from_api(srv),
        vm_names=vm_names,
    )
    await enqueue_task(env)
    return {"task_id": task_id}


@router.post("/astra-update", status_code=status.HTTP_202_ACCEPTED)
async def astra_update(
    payload: AstraUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(get_current_user),
    token: str = Depends(get_token),
):
    """
    Обновить Astra Linux на выбранных ВМ под указанный RC.

    Доступ: авторизованные пользователи с правом на конкретные ВМ; базовые ВМ — только админ.
    Валидация: `rc` обязателен и проверяется по списку доступных версий с удалённого сервера;
    все ВМ должны принадлежать одному серверу.
    Поведение: в очередь отправляется задача `vm.astra_update` c `vm_names`, `vms_full`, `rc` и `snapshot_name=rc`;
    после постановки задачи в БД регистрируется снимок с именем `rc` для каждой ВМ (если не существует).
    Ответ: идентификатор задачи и краткая сводка по созданным/пропущенным снимкам.
    Коды ошибок: 400, 403, 404, 500.
    """
    if not getattr(payload, "rc", None):
        raise HTTPException(status_code=400, detail="Field 'rc' is required")

    versions = await get_os_versions(token)
    allowed_rcs = {item["name"] for item in versions}
    if payload.rc not in allowed_rcs:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid RC '{payload.rc}'. Allowed RCs: {sorted(allowed_rcs)}"
        )

    if payload.ids:
        res = await db.execute(
            select(VirtualMachine).where(VirtualMachine.id.in_(payload.ids))
        )
        vms = res.scalars().all()
        found_ids = {vm.id for vm in vms}
        missing = [vid for vid in payload.ids if vid not in found_ids]
        if missing:
            raise HTTPException(status_code=404, detail=f"VM ids not found: {missing}")
    else:
        res = await db.execute(
            select(VirtualMachine).where(VirtualMachine.name.in_(payload.names))
        )
        vms = res.scalars().all()
        found_names = {vm.name for vm in vms}
        missing = [nm for nm in payload.names if nm not in found_names]
        if missing:
            raise HTTPException(status_code=404, detail=f"VM names not found: {missing}")

    if not vms:
        raise HTTPException(status_code=404, detail="No VMs selected")

    server_id = _ensure_same_server(vms)

    for vm in vms:
        ok, reason = _can_modify_vm(vm, user.login, getattr(user, "is_admin", False))
        if not ok:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden for VM id={vm.id}: {reason or 'forbidden'}"
            )

    srv = await get_physical_server_from_remote(server_id, token)

    vm_names = [vm.name for vm in vms]
    vms_full: Dict[str, Dict[str, object]] = {
        vm.name: {
            "cpu": vm.cpu,
            "ram": vm.ram,
            "host-port": "22",
            "ip_bridge": str(vm.ip_address),
        }
        for vm in vms
    }

    task_id = _uuid_task()
    env = _envelope(
        task_id=task_id,
        operation="vm.astra_update",
        server=_server_task_info_from_api(srv),
        vms_full=vms_full,
        vm_names=vm_names,
        rc=payload.rc,
        snapshot_name=payload.rc,
    )
    await enqueue_task(env)

    skipped: list[int] = []
    created: list[int] = []
    for vm in vms:
        try:
            snap = await create_snapshot(db, vm_id=vm.id, name=payload.rc)
            created.append(snap.id)
        except SnapshotAlreadyExists:
            skipped.append(vm.id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to register snapshot in DB for VM id={vm.id}: {e}")

    return {
        "task_id": task_id,
        "snapshots": {
            "created_count": len(created),
            "skipped_existing": skipped,
        },
    }

# TODO Добавить ендпоинт для массовой смены пароля при обновлении (продумать логику со снимаками)