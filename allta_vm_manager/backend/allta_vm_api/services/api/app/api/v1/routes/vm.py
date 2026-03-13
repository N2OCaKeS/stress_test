# app/api/v1/routes/vm.py
from __future__ import annotations
from ipaddress import ip_address
from typing import Any, Dict, List, Optional, Set, cast
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db
from app.api.v1.models.vm import VirtualMachine
from app.api.v1.models.vm_snapshot import VMSnapshot
from app.api.v1.schemas.vm import (
    VMRead, VMStatusUpdate, BatchVMCreateRequest, BatchVMUpdateRequest,
    VMDeleteRequest, AstraUpdateRequest, CreateDefaultVMsRequest,
    AlltaUpdateRequest, PasswdRefreshRequest, TaskEnvelope, TaskOperation,
    ServerTaskInfo, VMSpec, normalize_status,
)
from app.api.v1.dependencies import (
    AuthVerifyResponse,
    get_current_admin_user,
    get_current_user,
    get_token,
)
from app.utils.vm_helper import (
    ensure_server_ready_for_vms_hub, get_ip_range, get_range_bounds,
    ip_in_range, is_ip_free, is_name_free,
)
from app.utils.server_api import (
    get_os_versions,
    get_physical_server_from_remote,
    get_snapshot_password_by_os_version,
)
from app.utils.redis_queue import enqueue_task
from app.utils.crypto import Crypto
from app.utils.config import settings


router = APIRouter(prefix="/vm", tags=["VM"])
_CRYPTO = Crypto()

PRESET_VMS: Dict[str, Dict[str, Any]] = {
    "virtual-station1": {"ip": "10.177.103.101", "cpu": 16, "ram": 131072},
    "virtual-station2": {"ip": "10.177.103.102", "cpu": 16, "ram": 131072},
    "virtual-station3": {"ip": "10.177.103.103", "cpu": 16, "ram": 131072},
    "virtual-station4": {"ip": "10.177.103.104", "cpu": 16, "ram": 131072},
    "work-station1":    {"ip": "10.177.103.201", "cpu": 16, "ram": 131072},
    "work-station2":    {"ip": "10.177.103.202", "cpu": 16, "ram": 131072},
}
PRESET_NAMES: Set[str] = set(PRESET_VMS.keys())


def _uuid_task() -> str:
    return str(uuid.uuid4())


def _can_see_vm_passwords(user: AuthVerifyResponse) -> bool:
    return user.has_permission(settings.VM_MANAGE_PERMISSION) or user.has_permission(
        settings.SERVER_MANAGE_PERMISSION
    )


def _decrypted_vm_password(vm: VirtualMachine, *, reveal_password: bool) -> Optional[str]:
    token = cast(Optional[str], getattr(vm, "password_enc", None))
    if not reveal_password or not token:
        return None
    try:
        return _CRYPTO.decrypt(token)
    except Exception:
        return None


async def _server_task_info_from_api(srv, server_id: int, token: str) -> ServerTaskInfo:
    ip = str(getattr(srv, "ip_address", "") or getattr(srv, "admin_panel_ip", "")).strip()
    os_version_name = str(
        getattr(srv, "os_version", None) or getattr(srv, "os_version_name", None) or ""
    ).strip()
    phy_if_raw = str(getattr(srv, "phy_if", None) or getattr(srv, "phys_iface", None) or "").strip()

    if not ip:
        raise HTTPException(status_code=502, detail="Remote server has no IP field")
    if not os_version_name:
        raise HTTPException(status_code=502, detail="Remote server has no os_version")

    creds = await get_snapshot_password_by_os_version(os_version_name, token)
    username = str(creds.ssh_username).strip()
    password = str(creds.password)
    if not username or not password:
        raise HTTPException(
            status_code=502,
            detail=f"Snapshot password for os_version '{os_version_name}' is incomplete",
        )

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


def _vm_cpu(vm: VirtualMachine) -> int:
    return cast(int, vm.cpu)


def _vm_ram(vm: VirtualMachine) -> int:
    return cast(int, vm.ram)


def _vm_server_id(vm: VirtualMachine) -> int:
    return cast(int, vm.server_id)


def _vm_status(vm: VirtualMachine) -> Optional[str]:
    return cast(Optional[str], vm.status)


def _vm_to_read(vm: VirtualMachine, *, password: Optional[str]) -> VMRead:
    return VMRead(
        id=_vm_id(vm),
        name=_vm_name(vm),
        cpu=_vm_cpu(vm),
        ram=_vm_ram(vm),
        ip_address=ip_address(str(cast(Any, vm.ip_address))),
        server_id=_vm_server_id(vm),
        status=_vm_status(vm),
        password=password,
    )


def _env(*, task_id: str, operation: TaskOperation, server: ServerTaskInfo,
         vm_password: Optional[str] = None,
         new_password: Optional[str] = None,
         vms_full: Optional[Dict[str, VMSpec]] = None,
         vm_names: Optional[List[str]] = None,
         snapshot_name: Optional[str] = None,
         rc: Optional[str] = None) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=task_id,
        operation=operation,
        server=server,
        vm_password=vm_password,
        new_password=new_password,
        vms_full=vms_full,
        vm_names=vm_names,
        snapshot_name=snapshot_name,
        rc=rc,
        json_remote_path=f"/opt/allta_vm/jobs/{task_id}/{task_id}.json",
    )


# ---------- ХЕЛПЕРЫ ВАЛИДАЦИИ ----------

async def _assert_single_server_for_vms(
    db: AsyncSession,
    *,
    ids: Optional[List[int]] = None,
    names: Optional[List[str]] = None,
) -> List[VirtualMachine]:
    """Достаёт ВМ по ids/names, убеждается, что не пусто и все на одном сервере."""
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
    vms = list(res.scalars().all())
    if not vms:
        raise HTTPException(status_code=404, detail="No VMs found")

    server_ids = {_vm_server_id(vm) for vm in vms}
    if len(server_ids) != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Selected VMs belong to different servers: {sorted(server_ids)}"
        )
    return vms


async def _ensure_snapshots_exist_for_vms(db: AsyncSession, *, vms: List[VirtualMachine]) -> None:
    vm_ids = [_vm_id(vm) for vm in vms]
    res = await db.execute(
        select(VMSnapshot.vm_id).where(VMSnapshot.vm_id.in_(vm_ids))
    )
    existing_ids = set(res.scalars().all())
    missing = sorted(_vm_name(vm) for vm in vms if _vm_id(vm) not in existing_ids)
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "No snapshots found in DB for some VMs.",
                "vm_names": missing,
            },
        )


async def _build_vms_full_for_create(
    db: AsyncSession,
    payload: BatchVMCreateRequest,
) -> Dict[str, VMSpec]:
    """
    Проверяет пул IP (существование), границы диапазона,
    уникальность имени/адреса и собирает vms_full.
    """
    ipr = await get_ip_range(db, payload.ip_range_id)
    if not ipr:
        raise HTTPException(status_code=404, detail=f"IP range id={payload.ip_range_id} not found")

    start_ip, end_ip = get_range_bounds(ipr)

    errors: List[str] = []
    vms_full: Dict[str, VMSpec] = {}

    for name, item in payload.vms.items():
        ip_str = str(item.ip)

        # 1) имя свободно
        if not await is_name_free(db, name):
            errors.append(f"name {name!r} already exists")
            continue

        # 2) IP в пуле
        if not ip_in_range(ip_str, start_ip, end_ip):
            errors.append(f"ip {ip_str} is outside of range {start_ip}..{end_ip}")
            continue

        # 3) IP свободен
        if not await is_ip_free(db, ip_str):
            errors.append(f"ip {ip_str} is already in use")
            continue

        vms_full[name] = VMSpec(
            cpu=item.cpu,
            ram=item.ram,
            ip_bridge=ip_address(ip_str),
            server_id=payload.server_id,
        )

    if errors:
        raise HTTPException(status_code=400, detail={"skipped": errors})

    return vms_full


# ---------- CREATE ----------
@router.post("/create", status_code=status.HTTP_202_ACCEPTED,
             summary="Создать набор ВМ (ставит задачу в Redis; БД заполняет воркер)")
async def create(
    payload: BatchVMCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    token: str = Depends(get_token),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    # сервер готов
    ok, reason, _ = await ensure_server_ready_for_vms_hub(payload.server_id, token)
    if not ok:
        if reason and "not found" in reason:
            raise HTTPException(status_code=404, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "server not ready for vms hub")

    # пул и занятость IP — проверяем и собираем vms_full
    vms_full = await _build_vms_full_for_create(db, payload)

    # информация о сервере (для воркера)
    srv = await get_physical_server_from_remote(payload.server_id, token)

    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_create,
        server=await _server_task_info_from_api(srv, payload.server_id, token),
        vms_full=vms_full,
        vm_password=payload.password,
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


# ---------- CREATE DEFAULT ----------
@router.post("/create-default-vms/{server_id}", status_code=status.HTTP_202_ACCEPTED,
             summary="Создать набор базовых ВМ (ставит задачу в Redis; БД заполняет воркер)",
             dependencies=[Depends(get_current_admin_user)])
async def create_default_vms(
    server_id: int,
    body: CreateDefaultVMsRequest,
    token: str = Depends(get_token),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    srv = await get_physical_server_from_remote(server_id, token)

    vms_full: Dict[str, VMSpec] = {
        name: VMSpec(cpu=int(cfg["cpu"]), ram=int(cfg["ram"]),
                     ip_bridge=ip_address(str(cfg["ip"])), server_id=server_id)
        for name, cfg in PRESET_VMS.items()
    }

    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_base_create,
        server=await _server_task_info_from_api(srv, server_id, token),
        vms_full=vms_full,
        vm_password=body.password,
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


# ---------- READ ----------
@router.get("/{vm_id}", response_model=VMRead,
            summary="Получить ВМ по id (пароль расшифрован, если сохранён)")
async def get_vm_by_id(vm_id: int,
                       db: AsyncSession = Depends(get_async_db),
                       user: AuthVerifyResponse = Depends(get_current_user)):
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.id == vm_id))
    vm = res.scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    password = _decrypted_vm_password(
        vm,
        reveal_password=_can_see_vm_passwords(user),
    )

    return _vm_to_read(vm, password=password)


@router.get("/", response_model=List[VMRead],
            summary="Список ВМ (пароли расшифрованы, если сохранены)")
async def list_vms(db: AsyncSession = Depends(get_async_db),
                   user: AuthVerifyResponse = Depends(get_current_user),
                   skip: int = Query(0, ge=0),
                   limit: int = Query(100, gt=0, le=1000)):
    res = await db.execute(select(VirtualMachine).offset(skip).limit(limit))
    rows = res.scalars().all()
    out: List[VMRead] = []
    reveal_passwords = _can_see_vm_passwords(user)
    for vm in rows:
        pwd = _decrypted_vm_password(vm, reveal_password=reveal_passwords)
        out.append(_vm_to_read(vm, password=pwd))
    return out


# ---------- DELETE ----------
@router.delete("/", status_code=status.HTTP_202_ACCEPTED,
               summary="Удалить ВМ (ставит задачу; БД очищает воркер)")
async def delete_vms(payload: VMDeleteRequest,
                     db: AsyncSession = Depends(get_async_db),
                     _admin: AuthVerifyResponse = Depends(get_current_admin_user),
                     token: str = Depends(get_token)):
    vms = await _assert_single_server_for_vms(db, ids=payload.ids, names=payload.names)
    server_id = _vm_server_id(vms[0])
    srv = await get_physical_server_from_remote(server_id, token)

    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_delete,
        server=await _server_task_info_from_api(srv, server_id, token),
        vm_names=[_vm_name(vm) for vm in vms],
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


# ---------- UPDATE (ресурсы) ----------
@router.patch("/update", status_code=status.HTTP_202_ACCEPTED,
              summary="Обновить ресурсы ВМ (ставит задачу; БД обновляет воркер)")
async def batch_update_vms(payload: BatchVMUpdateRequest,
                           db: AsyncSession = Depends(get_async_db),
                           _admin: AuthVerifyResponse = Depends(get_current_admin_user),
                           token: str = Depends(get_token)):
    if not payload.vms:
        raise HTTPException(status_code=400, detail="Empty payload")

    # берём существующие ВМ по именам и валидируем единый сервер
    vms = await _assert_single_server_for_vms(db, names=list(payload.vms.keys()))
    server_id = _vm_server_id(vms[0])
    srv = await get_physical_server_from_remote(server_id, token)

    # собираем единообразный vms_full (только изменённые поля подставляем, остальные – из БД)
    current_by_name = {_vm_name(vm): vm for vm in vms}
    vms_full: Dict[str, VMSpec] = {}
    for name, patch in payload.vms.items():
        vm = current_by_name[name]
        cpu = patch.cpu if patch.cpu is not None else _vm_cpu(vm)
        ram = patch.ram if patch.ram is not None else _vm_ram(vm)
        vms_full[name] = VMSpec(cpu=cpu, ram=ram,
                                ip_bridge=ip_address(str(cast(Any, vm.ip_address))), server_id=_vm_server_id(vm))

    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_update,
        server=await _server_task_info_from_api(srv, server_id, token),
        vms_full=vms_full,
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


# ---------- POWER START ----------
@router.post("/start", status_code=status.HTTP_202_ACCEPTED, summary="Старт ВМ (ставит задачу)")
async def power_start_vms(payload: VMDeleteRequest,
                          db: AsyncSession = Depends(get_async_db),
                          _admin: AuthVerifyResponse = Depends(get_current_admin_user),
                          token: str = Depends(get_token)):
    vms = await _assert_single_server_for_vms(db, ids=payload.ids, names=payload.names)
    server_id = _vm_server_id(vms[0])
    srv = await get_physical_server_from_remote(server_id, token)

    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_start,
        server=await _server_task_info_from_api(srv, server_id, token),
        vm_names=[_vm_name(vm) for vm in vms],
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


# ---------- POWER STOP ----------
@router.post("/stop", status_code=status.HTTP_202_ACCEPTED, summary="Стоп ВМ (ставит задачу)")
async def power_stop_vms(payload: VMDeleteRequest,
                         db: AsyncSession = Depends(get_async_db),
                         _admin: AuthVerifyResponse = Depends(get_current_admin_user),
                         token: str = Depends(get_token)):
    vms = await _assert_single_server_for_vms(db, ids=payload.ids, names=payload.names)
    server_id = _vm_server_id(vms[0])
    srv = await get_physical_server_from_remote(server_id, token)

    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_stop,
        server=await _server_task_info_from_api(srv, server_id, token),
        vm_names=[_vm_name(vm) for vm in vms],
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


# ---------- ASTRA UPDATE ----------
@router.post("/astra-update", status_code=status.HTTP_202_ACCEPTED,
             summary="Обновление Astra Linux (ставит задачу)")
async def astra_update(payload: AstraUpdateRequest,
                       db: AsyncSession = Depends(get_async_db),
                       _admin: AuthVerifyResponse = Depends(get_current_admin_user),
                       token: str = Depends(get_token)):
    # 1) валидируем RC по внешнему API
    versions = await get_os_versions(token)
    allowed_rcs = {item["name"] for item in versions}
    if payload.rc not in allowed_rcs:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid RC '{payload.rc}'. Allowed RCs: {sorted(allowed_rcs)}"
        )

    # 2) валидируем, что все ВМ на одном сервере
    vms = await _assert_single_server_for_vms(db, ids=payload.ids, names=payload.names)
    server_id = _vm_server_id(vms[0])
    srv = await get_physical_server_from_remote(server_id, token)

    # 3) NEW: если хотя бы у одной ВМ уже есть снимок с таким RC — ошибка
    vm_ids = [_vm_id(vm) for vm in vms]
    by_id = {_vm_id(vm): _vm_name(vm) for vm in vms}
    res = await db.execute(
        select(VMSnapshot.vm_id).where(
            VMSnapshot.name == payload.rc,
            VMSnapshot.vm_id.in_(vm_ids),
        )
    )
    existing_ids = set(res.scalars().all())
    if existing_ids:
        conflict_vms = sorted(by_id[i] for i in existing_ids if i in by_id)
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Snapshot '{payload.rc}' already exists for some VMs.",
                "rc": payload.rc,
                "conflict_vms": conflict_vms,
            }
        )

    # 4) собираем vms_full для воркера
    vm_names = [_vm_name(vm) for vm in vms]
    vms_full: Dict[str, VMSpec] = {
        _vm_name(vm): VMSpec(
            cpu=_vm_cpu(vm),
            ram=_vm_ram(vm),
            ip_bridge=ip_address(str(cast(Any, vm.ip_address))),
            server_id=_vm_server_id(vm),
        )
        for vm in vms
    }

    # 5) ставим задачу
    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_astra_update,
        server=await _server_task_info_from_api(srv, server_id, token),
        vms_full=vms_full,
        vm_names=vm_names,
        rc=payload.rc,
        snapshot_name=payload.rc,
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


@router.post("/allta-update", status_code=status.HTTP_202_ACCEPTED,
             summary="Обновить allta на всех снимках выбранных ВМ (ставит задачу)")
async def allta_update(payload: AlltaUpdateRequest,
                       db: AsyncSession = Depends(get_async_db),
                       _admin: AuthVerifyResponse = Depends(get_current_admin_user),
                       token: str = Depends(get_token)):
    vms = await _assert_single_server_for_vms(db, ids=payload.ids, names=payload.names)
    await _ensure_snapshots_exist_for_vms(db, vms=vms)

    server_id = _vm_server_id(vms[0])
    srv = await get_physical_server_from_remote(server_id, token)

    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_allta_update,
        server=await _server_task_info_from_api(srv, server_id, token),
        vm_names=[_vm_name(vm) for vm in vms],
        new_password=payload.password,
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}


@router.post("/passwd", status_code=status.HTTP_202_ACCEPTED,
             summary="Обновить allta и сменить пароль на всех снимках выбранных ВМ (ставит задачу)")
async def passwd_refresh(payload: PasswdRefreshRequest,
                         db: AsyncSession = Depends(get_async_db),
                         _admin: AuthVerifyResponse = Depends(get_current_admin_user),
                         token: str = Depends(get_token)):
    vms = await _assert_single_server_for_vms(db, ids=payload.ids, names=payload.names)
    await _ensure_snapshots_exist_for_vms(db, vms=vms)

    server_id = _vm_server_id(vms[0])
    srv = await get_physical_server_from_remote(server_id, token)

    task_id = _uuid_task()
    env = _env(
        task_id=task_id,
        operation=TaskOperation.vm_allta_update,
        server=await _server_task_info_from_api(srv, server_id, token),
        vm_names=[_vm_name(vm) for vm in vms],
        new_password=payload.password,
    )
    await enqueue_task(env.model_dump(mode="json"))
    return {"task_id": task_id}




# ---------- STATUS ----------
@router.patch("/{vm_id}/status", response_model=VMRead,
              summary="Установить статус ВМ (fixed или логин).")
async def set_vm_status(vm_id: int, data: VMStatusUpdate,
                        db: AsyncSession = Depends(get_async_db),
                        user: AuthVerifyResponse = Depends(get_current_admin_user)):
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.id == vm_id))
    vm = res.scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    setattr(vm, "status", normalize_status(data.status))
    await db.commit()
    await db.refresh(vm)

    pwd = _decrypted_vm_password(
        vm,
        reveal_password=_can_see_vm_passwords(user),
    )

    return _vm_to_read(vm, password=pwd)


@router.post("/{vm_id}/release", response_model=VMRead,
             summary="Сбросить статус ВМ в 'free'.")
async def release_vm_status(vm_id: int,
                            db: AsyncSession = Depends(get_async_db),
                            user: AuthVerifyResponse = Depends(get_current_admin_user)):
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.id == vm_id))
    vm = res.scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    curr = (_vm_status(vm) or "").strip()
    if curr != "free":
        setattr(vm, "status", "free")

    await db.commit()
    await db.refresh(vm)

    pwd = _decrypted_vm_password(
        vm,
        reveal_password=_can_see_vm_passwords(user),
    )

    return _vm_to_read(vm, password=pwd)
