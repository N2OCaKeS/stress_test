"""Эндпоинты VM-домена: /vms (CRUD + питание + бронь + by-number) и
POST /servers/{id}/prepare-vms-hub.

Write-операции, которые дёргают воркера (create/power/delete/prepare-hub),
отвечают 202 с task_id (async-модель dispatch'а). Бронь (reserve/release/
status) — синхронный booking по полю `status`.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import PaginatedResponse
from src.schemas.vm import (
    VmCreate,
    VmDiskCreate,
    VmDiskDispatchResponse,
    VmDiskResizeRequest,
    VmDiskResponse,
    VmImageRefreshResponse,
    VmImageResponse,
    VmPowerRequest,
    VmReserveRequest,
    VmResponse,
    VmsHubPrepareResponse,
    VmStatusUpdate,
    VmTaskDispatchResponse,
    VmUpdateRequest,
)
from src.services import vm as svc
from src.services import vm_image_service as image_svc

router = APIRouter(prefix="/vms")
# prepare-vms-hub живёт под /servers/{id}; отдельный роутер, чтобы не тащить
# в servers.py VM-зависимости.
router_servers = APIRouter(prefix="/servers/{server_id}")
# Каталог боксов-образов ВМ (глобальный, зеркало FTP-конфига).
router_images = APIRouter(prefix="/vm-images")


@router.post(
    "",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Создать ВМ на hub-сервере (202, dispatch VM_CREATE)",
    description=(
        "Проверяет право `(vm, create)`, изоляцию отдела, готовность hub'а "
        "(`is_vms_hub`) и ёмкость hub'а (Σ vCPU/RAM/disk ВМ + запрос ≤ ресурсы "
        "hub'а → 409 VM_CAPACITY_EXCEEDED). Пишет карточку ВМ с "
        "`busy_state=creating` и диспатчит `vm.create` воркеру."
    ),
    responses={
        202: {"description": "ВМ создана, задача поставлена."},
        403: {"description": "Нет `create` либо чужой отдел (DEPARTMENT_ISOLATION)."},
        404: {"description": "HUB_NOT_FOUND — hub не найден / чужой отдел."},
        409: {"description": "HUB_NOT_PREPARED / VM_CAPACITY_EXCEEDED / VM_DUPLICATE."},
        503: {"description": "Worker недоступен."},
    },
)
async def create_vm(
    body: VmCreate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms — создать ВМ + dispatch VM_CREATE. Аудит: vm.created."""
    vm, task_id = await svc.create_vm(db, identity, request, body)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.patch(
    "/{vm_id}",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Изменить ресурсы ВМ (cpu/ram, 202, dispatch VM_UPDATE)",
    description=(
        "Меняет cpu и/или ram_mb. Гейтит право `(vm, update)`, бронь и "
        "lifecycle-lock; при увеличении проверяет ёмкость hub'а. Ставит "
        "`busy_state=updating` и диспатчит `vm.update` воркеру (stop→правка "
        "XML→start)."
    ),
    responses={
        202: {"description": "Задача поставлена, busy_state=updating."},
        403: {"description": "Нет `update`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / VM_CAPACITY_EXCEEDED / HUB_UNAVAILABLE."},
        422: {"description": "VM_UPDATE_EMPTY — не передан ни cpu, ни ram_mb."},
        503: {"description": "Worker недоступен."},
    },
)
async def update_vm(
    vm_id: str,
    body: VmUpdateRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """PATCH /vms/{id} — изменить cpu/ram + dispatch VM_UPDATE."""
    vm, task_id = await svc.update_vm(db, identity, request, vm_id, body)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.get(
    "",
    response_model=PaginatedResponse[VmResponse],
    summary="Список ВМ своего отдела",
    description=(
        "Offset-пагинация. Тип-wide `vm.view` даёт весь отдел; без него — "
        "grant-only листинг (только ВМ с инстанс-грантом). Без прав — 403."
    ),
)
async def list_vms(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[VmResponse]:
    """GET /vms — страница ВМ."""
    items, total = await svc.list_vms(db, identity, limit=limit, offset=offset)
    return PaginatedResponse[VmResponse](
        items=[VmResponse.from_vm(v) for v in items],
        total=total, limit=limit, offset=offset,
    )


@router.get(
    "/by-number/{number}",
    response_model=VmResponse,
    summary="Получить ВМ по номеру стенда",
    description="Номер глобально уникален в паре servers+vm. Не найдено / чужой отдел → 404.",
    responses={404: {"description": "VM_NOT_FOUND."}},
)
async def get_vm_by_number(
    number: int,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmResponse:
    """GET /vms/by-number/{number}."""
    vm = await svc.get_vm_by_number(db, identity, number)
    return VmResponse.from_vm(vm)


@router.get(
    "/{vm_id}",
    response_model=VmResponse,
    summary="Получить карточку ВМ",
    responses={404: {"description": "VM_NOT_FOUND / чужой отдел."}},
)
async def get_vm(
    vm_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmResponse:
    """GET /vms/{id}."""
    vm = await svc.get_vm(db, identity, vm_id)
    return VmResponse.from_vm(vm)


@router.delete(
    "/{vm_id}",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Удалить ВМ (202, dispatch VM_DELETE)",
    description=(
        "Гейтит право `(vm, delete)`, бронь и lifecycle-lock. Диспатчит "
        "`vm.delete` (чистка домена на гипервизоре) и сносит запись ВМ."
    ),
    responses={
        202: {"description": "Задача поставлена, запись удалена."},
        403: {"description": "Нет `delete`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def delete_vm(
    vm_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """DELETE /vms/{id}."""
    vm, task_id = await svc.delete_vm(db, identity, request, vm_id)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/power",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Питание ВМ (202, dispatch VM_POWER)",
    description=(
        "action ∈ start|shutdown|reboot|reset|destroy. Гейтит `(vm, vm_power)`, "
        "бронь и lifecycle-lock. Диспатчит `vm.power` воркеру."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `vm_power`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        422: {"description": "INVALID_VM_POWER_ACTION."},
        503: {"description": "Worker недоступен."},
    },
)
async def power_vm(
    vm_id: str,
    body: VmPowerRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms/{id}/power."""
    vm, task_id = await svc.power_vm(db, identity, request, vm_id, body.action)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/reserve",
    response_model=VmResponse,
    summary="Забронировать ВМ под тест",
    description="status → run test / debug test / свой логин. Гейтит `(vm, vm_reserve)`.",
    responses={
        403: {"description": "Нет `vm_reserve`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_ALREADY_RESERVED."},
    },
)
async def reserve_vm(
    vm_id: str,
    identity: CurrentUserIdentity,
    body: VmReserveRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> VmResponse:
    """POST /vms/{id}/reserve."""
    payload = body if body is not None else VmReserveRequest()
    vm = await svc.reserve_vm(db, identity, vm_id, payload.status)
    return VmResponse.from_vm(vm)


@router.post(
    "/{vm_id}/release",
    response_model=VmResponse,
    summary="Снять бронь ВМ (status → free)",
    responses={
        403: {"description": "Нет `vm_release`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_RESERVED — занята другим (не владелец/не админ)."},
    },
)
async def release_vm(
    vm_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmResponse:
    """POST /vms/{id}/release."""
    vm = await svc.release_vm(db, identity, vm_id)
    return VmResponse.from_vm(vm)


@router.patch(
    "/{vm_id}/status",
    response_model=VmResponse,
    summary="Выставить booking-статус ВМ (free / run test / debug test / <login>)",
    responses={
        403: {"description": "Нет `vm_reserve`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_RESERVED — перебить чужую бронь нельзя."},
    },
)
async def set_vm_status(
    vm_id: str,
    body: VmStatusUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmResponse:
    """PATCH /vms/{id}/status."""
    vm = await svc.set_status(db, identity, vm_id, body.status)
    return VmResponse.from_vm(vm)


@router_servers.post(
    "/prepare-vms-hub",
    response_model=VmsHubPrepareResponse,
    status_code=202,
    summary="Подготовить сервер как VMS-hub (202, dispatch VMS_HUB_PREPARE)",
    description=(
        "Гейтит право `(vm, vms_hub_prepare)`. Сервер обязан быть prepared "
        "(`is_managed`) и поддерживать виртуализацию (`virtualization=True`), "
        "иначе 409. Диспатчит `vms_hub.prepare` воркеру."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `vms_hub_prepare`."},
        404: {"description": "SERVER_NOT_FOUND / чужой отдел."},
        409: {"description": "PREPARE_REQUIRED / VIRTUALIZATION_NOT_SUPPORTED."},
        503: {"description": "Worker недоступен."},
    },
)
async def prepare_vms_hub(
    server_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmsHubPrepareResponse:
    """POST /servers/{id}/prepare-vms-hub."""
    sid, task_id = await svc.prepare_vms_hub(db, identity, request, server_id)
    return VmsHubPrepareResponse(server_id=sid, task_id=task_id, status="queued")


# ── диски ВМ ─────────────────────────────────────────────────────────────────


@router.get(
    "/{vm_id}/disks",
    response_model=list[VmDiskResponse],
    summary="Список дисков ВМ",
    description="Гейтит право `(vm, view)`. Cross-dept / нет ВМ → 404.",
    responses={403: {"description": "Нет `view`."}, 404: {"description": "VM_NOT_FOUND."}},
)
async def list_vm_disks(
    vm_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[VmDiskResponse]:
    """GET /vms/{id}/disks."""
    disks = await svc.list_disks(db, identity, vm_id)
    return [VmDiskResponse.model_validate(d) for d in disks]


@router.post(
    "/{vm_id}/disks",
    response_model=VmDiskDispatchResponse,
    status_code=202,
    summary="Создать и подключить диск ВМ (202, dispatch VM_DISK_ATTACH)",
    description=(
        "Гейтит право `(vm, vm_disk_manage)`, бронь и lifecycle-lock. Пишет "
        "строку диска (`state=creating`) и диспатчит `vm.disk_attach` воркеру "
        "(qemu-img create + attach-disk --persistent --targetbus virtio "
        "--serial <vm>_<disk>)."
    ),
    responses={
        202: {"description": "Задача поставлена, строка диска создана."},
        403: {"description": "Нет `vm_disk_manage`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / VM_DISK_DUPLICATE / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def create_vm_disk(
    vm_id: str,
    body: VmDiskCreate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmDiskDispatchResponse:
    """POST /vms/{id}/disks."""
    disk, task_id = await svc.create_disk(db, identity, request, vm_id, body)
    return VmDiskDispatchResponse(vm_id=disk.vm_id, disk_id=disk.id, task_id=task_id, status="queued")


@router.delete(
    "/{vm_id}/disks/{disk_id}",
    response_model=VmDiskDispatchResponse,
    status_code=202,
    summary="Отключить и удалить диск ВМ (202, dispatch VM_DISK_DELETE)",
    description=(
        "Гейтит право `(vm, vm_disk_manage)`, бронь и lifecycle-lock. Диспатчит "
        "`vm.disk_delete` (detach + rm qcow2) и сносит строку диска."
    ),
    responses={
        202: {"description": "Задача поставлена, строка диска удалена."},
        403: {"description": "Нет `vm_disk_manage`."},
        404: {"description": "VM_NOT_FOUND / VM_DISK_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def delete_vm_disk(
    vm_id: str,
    disk_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmDiskDispatchResponse:
    """DELETE /vms/{id}/disks/{disk_id}."""
    disk, task_id = await svc.delete_disk(db, identity, request, vm_id, disk_id)
    return VmDiskDispatchResponse(vm_id=disk.vm_id, disk_id=disk.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/disks/{disk_id}/resize",
    response_model=VmDiskDispatchResponse,
    status_code=202,
    summary="Расширить диск ВМ (202, dispatch VM_DISK_RESIZE)",
    description=(
        "Только увеличение. Гейтит право `(vm, vm_disk_manage)`, бронь и "
        "lifecycle-lock. Диспатчит `vm.disk_resize` (qemu-img resize + growpart/"
        "resize2fs в госте)."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `vm_disk_manage`."},
        404: {"description": "VM_NOT_FOUND / VM_DISK_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        422: {"description": "VM_DISK_SHRINK_FORBIDDEN — новый размер не больше текущего."},
        503: {"description": "Worker недоступен."},
    },
)
async def resize_vm_disk(
    vm_id: str,
    disk_id: str,
    body: VmDiskResizeRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmDiskDispatchResponse:
    """POST /vms/{id}/disks/{disk_id}/resize."""
    disk, task_id = await svc.resize_disk(db, identity, request, vm_id, disk_id, body.size_gb)
    return VmDiskDispatchResponse(vm_id=disk.vm_id, disk_id=disk.id, task_id=task_id, status="queued")


# ── каталог боксов-образов ───────────────────────────────────────────────────


@router_images.get(
    "",
    response_model=PaginatedResponse[VmImageResponse],
    summary="Список боксов-образов ВМ (каталог)",
    description="Глобальный каталог образов. Гейтит право `(vm, view)`.",
    responses={403: {"description": "Нет `view`."}},
)
async def list_vm_images(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[VmImageResponse]:
    """GET /vm-images."""
    items, total = await image_svc.list_images(db, identity, limit=limit, offset=offset)
    return PaginatedResponse[VmImageResponse](
        items=[VmImageResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router_images.post(
    "/refresh",
    response_model=VmImageRefreshResponse,
    summary="Синхронизировать каталог образов с FTP-конфигом",
    description=(
        "Тянет `test-box-config.json` с FTP (секция `libvirt_box`) и upsert'ит "
        "глобальные образы. Гейтит право `(vm, vm_preset_manage)`. Недоступный/"
        "битый конфиг → 503."
    ),
    responses={
        200: {"description": "Каталог синхронизирован."},
        403: {"description": "Нет `vm_preset_manage`."},
        503: {"description": "VM_BOX_CONFIG_UNAVAILABLE / VM_BOX_CONFIG_INVALID."},
    },
)
async def refresh_vm_images(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmImageRefreshResponse:
    """POST /vm-images/refresh."""
    data = await image_svc.refresh_catalog(db, identity)
    return VmImageRefreshResponse(**data)
