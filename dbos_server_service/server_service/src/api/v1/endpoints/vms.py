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
    CreateDefaultVmItem,
    CreateDefaultVmSkipped,
    CreateDefaultVmsResponse,
    VmAlltaUpdateRequest,
    VmAstraUpdateRequest,
    VmAutostartRequest,
    VmAvailableIpsResponse,
    VmConsoleRequest,
    VmConsoleResponse,
    VmCreate,
    VmDiskCreate,
    VmDiskDispatchResponse,
    VmDiskResizeRequest,
    VmDiskResponse,
    VmImageRefreshResponse,
    VmImageResponse,
    VmIpPoolCreate,
    VmIpPoolResponse,
    VmIpPoolUpdate,
    VmNetworkRequest,
    VmPasswdRequest,
    VmPowerRequest,
    VmPresetCreate,
    VmPresetResponse,
    VmPresetUpdate,
    VmReserveRequest,
    VmCredStrategyRequest,
    VmResponse,
    VmsHubPrepareResponse,
    VmsHubTeardownResponse,
    VmSnapshotCreate,
    VmSnapshotDispatchResponse,
    VmSnapshotResponse,
    VmStatusUpdate,
    VmTaskDispatchResponse,
    VmUpdateRequest,
)
from src.services import vm as svc
from src.services import vm_image_service as image_svc
from src.services import vm_ip_pool as ip_pool_svc
from src.services import vm_preset as preset_svc

router = APIRouter(prefix="/vms")
# prepare-vms-hub / create-default-vms / teardown живут под /servers/{id};
# отдельный роутер, чтобы не тащить в servers.py VM-зависимости.
router_servers = APIRouter(prefix="/servers/{server_id}")
# Каталог боксов-образов ВМ (глобальный, зеркало FTP-конфига).
router_images = APIRouter(prefix="/vm-images")
# Пулы IP-адресов ВМ (IPAM, глобальный CRUD под правом vm.net_manage).
router_ip_pools = APIRouter(prefix="/vm-ip-pools")
# Пресеты стандартных ВМ (CRUD под правом vm.preset_manage).
router_presets = APIRouter(prefix="/vm-presets")


@router.post(
    "",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Создать ВМ на hub-сервере (202, dispatch VM_CREATE)",
    description=(
        "Проверяет право `(vm, create)`, изоляцию отдела, готовность hub'а "
        "(`is_vms_hub`) и ёмкость hub'а (Σ vCPU/RAM/disk ВМ + запрос ≤ ресурсы "
        "hub'а → 409 VM_CAPACITY_EXCEEDED). Для bridge разрешает IP: заданный "
        "`ip_address` (проверка занятости) либо авто-выбор из `pool_id`; без "
        "адреса и пула → 400 VM_BRIDGE_IP_REQUIRED. Пишет карточку ВМ с "
        "`busy_state=creating` и диспатчит `vm.create` воркеру."
    ),
    responses={
        202: {"description": "ВМ создана, задача поставлена."},
        400: {"description": "VM_BOX_NOT_IN_CATALOG / VM_BRIDGE_IP_REQUIRED — bridge без ip_address и без пула."},
        403: {"description": "Нет `create` либо чужой отдел (DEPARTMENT_ISOLATION)."},
        404: {"description": "HUB_NOT_FOUND — hub не найден / чужой отдел / VM_IP_POOL_NOT_FOUND."},
        409: {"description": "HUB_NOT_PREPARED / VM_CAPACITY_EXCEEDED / VM_IP_IN_USE / VM_IP_POOL_EXHAUSTED / VM_DUPLICATE."},
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


@router.patch(
    "/{vm_id}/cred-strategy",
    response_model=VmResponse,
    summary="Сменить режим управляющих кред ВМ (per_snapshot/reroll, синхронно)",
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "VM_NOT_FOUND."},
    },
)
async def set_vm_cred_strategy(
    vm_id: str,
    body: VmCredStrategyRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmResponse:
    """PATCH /vms/{id}/cred-strategy — режим mgmt-кред ВМ (синхронно, без задачи)."""
    vm = await svc.set_cred_strategy(db, identity, request, vm_id, body)
    return VmResponse.model_validate(vm)


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
    "/available-ips",
    response_model=VmAvailableIpsResponse,
    summary="Свободные IP пула (IPAM-аллокатор в режиме read)",
    description=(
        "Возвращает свободные адреса пула `pool_id` (диапазон за вычетом gateway "
        "и занятых `vms.ip_address` отдела). Право `(vm, vm_net_manage)`."
    ),
    responses={
        403: {"description": "Нет `vm_net_manage`."},
        404: {"description": "VM_IP_POOL_NOT_FOUND / чужой отдел."},
    },
)
async def list_available_ips(
    identity: CurrentUserIdentity,
    pool_id: str = Query(..., description="ID пула IP-адресов ВМ."),
    db: AsyncSession = Depends(get_db),
) -> VmAvailableIpsResponse:
    """GET /vms/available-ips?pool_id=."""
    data = await ip_pool_svc.available_ips(db, identity, pool_id)
    return VmAvailableIpsResponse(**data)


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
    "/{vm_id}/autostart",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Автозапуск ВМ при старте hub'а (202, dispatch VM_SET_AUTOSTART)",
    description=(
        "Включает/выключает автозапуск ВМ (`virsh autostart [--disable]`). "
        "Гейтит право `(vm, vm_power)`, бронь и lifecycle-lock. Флаг autostart "
        "выставляется оптимистично, воркер применяет его в libvirt."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `vm_power`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def set_vm_autostart(
    vm_id: str,
    body: VmAutostartRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms/{id}/autostart."""
    vm, task_id = await svc.set_autostart(db, identity, request, vm_id, body.enabled)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/console",
    response_model=VmConsoleResponse,
    summary="Доступ к консоли ВМ (ssh / vnc / serial)",
    description=(
        "Выдаёт контракт подключения к консоли ВМ: короткоживущий токен + "
        "hub-хост + порт/serial-путь/пользователь по типу консоли. Гейтит право "
        "`(vm, view)` и бронь. Реальный проброс держит отдельный websockify/PTY-"
        "прокси; server_service токен не хранит и plaintext-креды не отдаёт."
    ),
    responses={
        200: {"description": "Контракт подключения к консоли."},
        403: {"description": "Нет `view`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_RESERVED / HUB_UNAVAILABLE."},
        422: {"description": "INVALID_VM_CONSOLE_KIND."},
    },
)
async def vm_console(
    vm_id: str,
    identity: CurrentUserIdentity,
    body: VmConsoleRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> VmConsoleResponse:
    """POST /vms/{id}/console."""
    payload = body if body is not None else VmConsoleRequest()
    data = await svc.console_access(db, identity, vm_id, payload.kind)
    return VmConsoleResponse(**data)


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


@router_servers.post(
    "/create-default-vms",
    response_model=CreateDefaultVmsResponse,
    status_code=202,
    summary="Развернуть пресеты отдела на hub (202, серия dispatch VM_CREATE)",
    description=(
        "Разворачивает все пресеты отдела на hub'е (по одной ВМ на пресет). "
        "Deploy-once: bridge-пресет — 1 раз глобально, nat-пресет — 1 раз на "
        "hub-сервер; уже развёрнутые пропускаются (skipped). Полный повтор → "
        "409 VM_PRESETS_ALREADY_DEPLOYED. Ёмкость hub'а проверяется по сумме "
        "разворачиваемых пресетов. Гейтит право `(vm, create)`."
    ),
    responses={
        202: {"description": "Пресеты развёрнуты (задачи vm.create поставлены)."},
        403: {"description": "Нет `create`."},
        404: {"description": "HUB_NOT_FOUND / VM_NO_PRESETS."},
        409: {"description": "HUB_NOT_PREPARED / VM_CAPACITY_EXCEEDED / VM_PRESETS_ALREADY_DEPLOYED / VM_DUPLICATE."},
        503: {"description": "Worker недоступен."},
    },
)
async def create_default_vms(
    server_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CreateDefaultVmsResponse:
    """POST /servers/{id}/create-default-vms."""
    sid, created, skipped = await svc.create_default_vms(db, identity, request, server_id)
    return CreateDefaultVmsResponse(
        server_id=sid,
        created=[CreateDefaultVmItem(**c) for c in created],
        skipped=[CreateDefaultVmSkipped(**s) for s in skipped],
        status="queued",
    )


@router_servers.delete(
    "/vms-hub",
    response_model=VmsHubTeardownResponse,
    status_code=202,
    summary="Снять сервер с роли VMS-hub (202, dispatch VMS_HUB_TEARDOWN)",
    description=(
        "Симметрия старому rm-vms-hub: карточки ВМ отдела на hub'е сносятся из "
        "БД сразу (диски/снимки — каскадом), hub → free (`is_vms_hub=False`), "
        "воркеру диспатчится `vms_hub.teardown` для очистки хоста. Право "
        "`(vm, vms_hub_prepare)` (привилегированное)."
    ),
    responses={
        202: {"description": "Hub снят с роли, задача очистки поставлена."},
        403: {"description": "Нет `vms_hub_prepare`."},
        404: {"description": "SERVER_NOT_FOUND / чужой отдел."},
        409: {"description": "NOT_A_VMS_HUB — сервер не является VMS-hub'ом."},
        503: {"description": "Worker недоступен."},
    },
)
async def teardown_vms_hub(
    server_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmsHubTeardownResponse:
    """DELETE /servers/{id}/vms-hub."""
    sid, task_id, removed = await svc.teardown_vms_hub(db, identity, request, server_id)
    return VmsHubTeardownResponse(
        server_id=sid, task_id=task_id, vms_removed=removed, status="queued",
    )


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


# ── снимки ВМ ────────────────────────────────────────────────────────────────


@router.get(
    "/{vm_id}/snapshots",
    response_model=list[VmSnapshotResponse],
    summary="Список снимков ВМ (системные _build скрыты)",
    description="Гейтит право `(vm, view)`. Системные golden-снимки (`<ver>_build`) в выдаче не показываются. Cross-dept / нет ВМ → 404.",
    responses={403: {"description": "Нет `view`."}, 404: {"description": "VM_NOT_FOUND."}},
)
async def list_vm_snapshots(
    vm_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[VmSnapshotResponse]:
    """GET /vms/{id}/snapshots."""
    snapshots = await svc.list_snapshots(db, identity, vm_id)
    return [VmSnapshotResponse.model_validate(s) for s in snapshots]


@router.post(
    "/{vm_id}/snapshots",
    response_model=VmSnapshotDispatchResponse,
    status_code=202,
    summary="Снять снимок ВМ (202, dispatch VM_SNAPSHOT_CREATE)",
    description=(
        "Гейтит право `(vm, vm_snapshot_manage)`, бронь и lifecycle-lock. Имя с "
        "суффиксом `_build` руками завести нельзя (403 — зарезервировано). Пишет "
        "строку снимка (`state=creating`) и диспатчит `vm.snapshot_create`. В "
        "режиме per_snapshot новый снимок наследует mgmt-креды текущего."
    ),
    responses={
        202: {"description": "Задача поставлена, строка снимка создана."},
        403: {"description": "Нет `vm_snapshot_manage` / VM_SNAPSHOT_SYSTEM_PROTECTED."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / VM_SNAPSHOT_EXISTS / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def create_vm_snapshot(
    vm_id: str,
    body: VmSnapshotCreate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmSnapshotDispatchResponse:
    """POST /vms/{id}/snapshots."""
    snapshot, task_id = await svc.create_snapshot(db, identity, request, vm_id, body)
    return VmSnapshotDispatchResponse(vm_id=snapshot.vm_id, snapshot_id=snapshot.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/snapshots/{snapshot_id}/revert",
    response_model=VmSnapshotDispatchResponse,
    status_code=202,
    summary="Откатить ВМ к снимку (202, dispatch VM_SNAPSHOT_REVERT)",
    description=(
        "Гейтит право `(vm, vm_snapshot_manage)`, бронь и lifecycle-lock. "
        "Системные `<ver>_build` откатывать руками нельзя (403). В режиме "
        "per_snapshot активные креды ВМ переключаются на снимковые."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `vm_snapshot_manage` / VM_SNAPSHOT_SYSTEM_PROTECTED."},
        404: {"description": "VM_NOT_FOUND / VM_SNAPSHOT_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def revert_vm_snapshot(
    vm_id: str,
    snapshot_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmSnapshotDispatchResponse:
    """POST /vms/{id}/snapshots/{snapshot_id}/revert."""
    snapshot, task_id = await svc.revert_snapshot(db, identity, request, vm_id, snapshot_id)
    return VmSnapshotDispatchResponse(vm_id=snapshot.vm_id, snapshot_id=snapshot.id, task_id=task_id, status="queued")


@router.delete(
    "/{vm_id}/snapshots/{snapshot_id}",
    response_model=VmSnapshotDispatchResponse,
    status_code=202,
    summary="Удалить снимок ВМ (202, dispatch VM_SNAPSHOT_DELETE)",
    description=(
        "Гейтит право `(vm, vm_snapshot_manage)`, бронь и lifecycle-lock. "
        "Системные `<ver>_build` удалять руками нельзя (403). Диспатчит "
        "`vm.snapshot_delete` и сносит строку снимка."
    ),
    responses={
        202: {"description": "Задача поставлена, строка снимка удалена."},
        403: {"description": "Нет `vm_snapshot_manage` / VM_SNAPSHOT_SYSTEM_PROTECTED."},
        404: {"description": "VM_NOT_FOUND / VM_SNAPSHOT_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def delete_vm_snapshot(
    vm_id: str,
    snapshot_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmSnapshotDispatchResponse:
    """DELETE /vms/{id}/snapshots/{snapshot_id}."""
    snapshot, task_id = await svc.delete_snapshot(db, identity, request, vm_id, snapshot_id)
    return VmSnapshotDispatchResponse(vm_id=snapshot.vm_id, snapshot_id=snapshot.id, task_id=task_id, status="queued")


# ── обновления ОС / гостевой allta / пароль ──────────────────────────────────


@router.post(
    "/{vm_id}/astra-update",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Обновить ОС ВМ по RC (202, dispatch VM_ASTRA_UPDATE)",
    description=(
        "Гейтит право `(vm, vm_astra_update)`, бронь и lifecycle-lock. Снимок с "
        "именем `rc` не должен существовать (409). repository_urls берутся из "
        "зарегистрированной OS-версии `rc` (404, если не заведена). Ставит "
        "busy_state=updating и диспатчит `vm.astra_update`."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `vm_astra_update`."},
        404: {"description": "VM_NOT_FOUND / OS_VERSION_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / VM_SNAPSHOT_EXISTS / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def astra_update_vm(
    vm_id: str,
    body: VmAstraUpdateRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms/{id}/astra-update."""
    vm, task_id = await svc.astra_update(db, identity, request, vm_id, body)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/allta-update",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Обновить гостевую allta + опц. пароль (202, dispatch VM_ALLTA_UPDATE)",
    description=(
        "Гейтит право `(vm, vm_allta_update)`, бронь и lifecycle-lock. Пароль "
        "опционален. Диспатчит `vm.allta_update` (обновление guest-allta .deb + "
        "опц. смена пароля `u`; в reroll — по всем не-`_build` снимкам)."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `vm_allta_update`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def allta_update_vm(
    vm_id: str,
    body: VmAlltaUpdateRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms/{id}/allta-update."""
    vm, task_id = await svc.allta_update(db, identity, request, vm_id, body)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/passwd",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Сменить пароль гостевого `u` (202, dispatch VM_PASSWD)",
    description=(
        "Тот же op, что allta-update, но пароль обязателен (пустой → 422). "
        "Гейтит право `(vm, vm_passwd)`, бронь и lifecycle-lock."
    ),
    responses={
        202: {"description": "Задача поставлена."},
        403: {"description": "Нет `vm_passwd`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / HUB_UNAVAILABLE."},
        422: {"description": "password пуст/не передан."},
        503: {"description": "Worker недоступен."},
    },
)
async def passwd_vm(
    vm_id: str,
    body: VmPasswdRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms/{id}/passwd."""
    vm, task_id = await svc.passwd(db, identity, request, vm_id, body)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


# ── prepare / mgmt-креды / сеть ──────────────────────────────────────────────


@router.post(
    "/{vm_id}/prepare",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Онбординг управления ВМ (202, dispatch VM_PREPARE)",
    description=(
        "Гейтит право `(vm, vm_prepare)`, бронь и lifecycle-lock. server_service "
        "генерит per-VM управляющую SSH-пару + пароль, шифрует и отдаёт их "
        "воркеру вместе с дефолт-кредами образа (`u:1`); воркер ставит их в "
        "госте и подтверждает callback'ом. Ставит busy_state=preparing."
    ),
    responses={
        202: {"description": "Задача поставлена, busy_state=preparing."},
        403: {"description": "Нет `vm_prepare`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / VM_MGMT_ROTATION_PENDING / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def prepare_vm(
    vm_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms/{id}/prepare."""
    vm, task_id = await svc.prepare_vm(db, identity, request, vm_id)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/mgmt-creds/rotate",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Ротация per-VM управляющих кред (202, dispatch VM_PREPARE)",
    description=(
        "Гейтит право `(vm, vm_prepare)`, бронь и lifecycle-lock. ВМ обязана "
        "быть prepared (`is_managed`), иначе 409 VM_PREPARE_REQUIRED. Генерит "
        "новый управляющий материал, шифрует и отдаёт воркеру для установки. "
        "409 VM_MGMT_ROTATION_PENDING, если предыдущая ротация не подтверждена."
    ),
    responses={
        202: {"description": "Задача поставлена, busy_state=preparing."},
        403: {"description": "Нет `vm_prepare`."},
        404: {"description": "VM_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / VM_PREPARE_REQUIRED / VM_MGMT_ROTATION_PENDING / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def rotate_vm_mgmt_creds(
    vm_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms/{id}/mgmt-creds/rotate."""
    vm, task_id = await svc.rotate_mgmt_creds(db, identity, request, vm_id)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


@router.post(
    "/{vm_id}/network",
    response_model=VmTaskDispatchResponse,
    status_code=202,
    summary="Сменить сетевой режим ВМ (202, dispatch VM_SET_NETWORK)",
    description=(
        "Гейтит право `(vm, vm_net_manage)`, бронь и lifecycle-lock. bridge: "
        "адрес из `ip_address` (проверяется на занятость) либо аллоцируется из "
        "`pool_id`; nat: адрес выдаёт libvirt. Ставит busy_state=networking."
    ),
    responses={
        202: {"description": "Задача поставлена, busy_state=networking."},
        403: {"description": "Нет `vm_net_manage`."},
        404: {"description": "VM_NOT_FOUND / VM_IP_POOL_NOT_FOUND."},
        409: {"description": "VM_BUSY / VM_RESERVED / VM_IP_IN_USE / VM_IP_POOL_EXHAUSTED / HUB_UNAVAILABLE."},
        503: {"description": "Worker недоступен."},
    },
)
async def set_vm_network(
    vm_id: str,
    body: VmNetworkRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmTaskDispatchResponse:
    """POST /vms/{id}/network."""
    vm, task_id = await svc.set_network(db, identity, request, vm_id, body)
    return VmTaskDispatchResponse(vm_id=vm.id, task_id=task_id, status="queued")


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


# ── IPAM: пулы IP-адресов ВМ ─────────────────────────────────────────────────


@router_ip_pools.get(
    "",
    response_model=PaginatedResponse[VmIpPoolResponse],
    summary="Список пулов IP-адресов ВМ своего отдела",
    description="Гейтит право `(vm, vm_net_manage)`.",
    responses={403: {"description": "Нет `vm_net_manage`."}},
)
async def list_ip_pools(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[VmIpPoolResponse]:
    """GET /vm-ip-pools."""
    items, total = await ip_pool_svc.list_pools(db, identity, limit=limit, offset=offset)
    return PaginatedResponse[VmIpPoolResponse](
        items=[VmIpPoolResponse.model_validate(p) for p in items],
        total=total, limit=limit, offset=offset,
    )


@router_ip_pools.post(
    "",
    response_model=VmIpPoolResponse,
    status_code=201,
    summary="Создать пул IP-адресов ВМ",
    description="Гейтит право `(vm, vm_net_manage)`, изоляцию отдела, валидацию диапазона.",
    responses={
        403: {"description": "Нет `vm_net_manage` / DEPARTMENT_ISOLATION."},
        409: {"description": "VM_IP_POOL_DUPLICATE."},
        422: {"description": "VM_IP_POOL_INVALID — диапазон/gateway вне cidr либо start>end."},
    },
)
async def create_ip_pool(
    body: VmIpPoolCreate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmIpPoolResponse:
    """POST /vm-ip-pools."""
    pool = await ip_pool_svc.create_pool(db, identity, request, body)
    return VmIpPoolResponse.model_validate(pool)


@router_ip_pools.get(
    "/{pool_id}",
    response_model=VmIpPoolResponse,
    summary="Получить пул IP-адресов ВМ",
    responses={
        403: {"description": "Нет `vm_net_manage`."},
        404: {"description": "VM_IP_POOL_NOT_FOUND / чужой отдел."},
    },
)
async def get_ip_pool(
    pool_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmIpPoolResponse:
    """GET /vm-ip-pools/{id}."""
    pool = await ip_pool_svc.get_pool(db, identity, pool_id)
    return VmIpPoolResponse.model_validate(pool)


@router_ip_pools.patch(
    "/{pool_id}",
    response_model=VmIpPoolResponse,
    summary="Изменить пул IP-адресов ВМ (частично)",
    responses={
        403: {"description": "Нет `vm_net_manage`."},
        404: {"description": "VM_IP_POOL_NOT_FOUND / чужой отдел."},
        409: {"description": "VM_IP_POOL_DUPLICATE."},
        422: {"description": "VM_IP_POOL_UPDATE_EMPTY / VM_IP_POOL_INVALID."},
    },
)
async def update_ip_pool(
    pool_id: str,
    body: VmIpPoolUpdate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmIpPoolResponse:
    """PATCH /vm-ip-pools/{id}."""
    pool = await ip_pool_svc.update_pool(db, identity, request, pool_id, body)
    return VmIpPoolResponse.model_validate(pool)


@router_ip_pools.delete(
    "/{pool_id}",
    status_code=204,
    summary="Удалить пул IP-адресов ВМ",
    responses={
        403: {"description": "Нет `vm_net_manage`."},
        404: {"description": "VM_IP_POOL_NOT_FOUND / чужой отдел."},
    },
)
async def delete_ip_pool(
    pool_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> None:
    """DELETE /vm-ip-pools/{id}."""
    await ip_pool_svc.delete_pool(db, identity, pool_id)


# ── пресеты стандартных ВМ (vm_preset) ───────────────────────────────────────


@router_presets.get(
    "",
    response_model=PaginatedResponse[VmPresetResponse],
    summary="Список пресетов стандартных ВМ своего отдела",
    description="Гейтит право `(vm, vm_preset_manage)`.",
    responses={403: {"description": "Нет `vm_preset_manage`."}},
)
async def list_presets(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[VmPresetResponse]:
    """GET /vm-presets."""
    items, total = await preset_svc.list_presets(db, identity, limit=limit, offset=offset)
    return PaginatedResponse[VmPresetResponse](
        items=[VmPresetResponse.model_validate(p) for p in items],
        total=total, limit=limit, offset=offset,
    )


@router_presets.post(
    "",
    response_model=VmPresetResponse,
    status_code=201,
    summary="Создать пресет стандартной ВМ",
    description="Гейтит право `(vm, vm_preset_manage)`, изоляцию отдела.",
    responses={
        403: {"description": "Нет `vm_preset_manage`."},
        409: {"description": "DEPARTMENT_ISOLATION / VM_PRESET_DUPLICATE."},
    },
)
async def create_preset(
    body: VmPresetCreate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmPresetResponse:
    """POST /vm-presets."""
    preset = await preset_svc.create_preset(db, identity, request, body)
    return VmPresetResponse.model_validate(preset)


@router_presets.get(
    "/{preset_id}",
    response_model=VmPresetResponse,
    summary="Получить пресет стандартной ВМ",
    responses={
        403: {"description": "Нет `vm_preset_manage`."},
        404: {"description": "VM_PRESET_NOT_FOUND / чужой отдел."},
    },
)
async def get_preset(
    preset_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmPresetResponse:
    """GET /vm-presets/{id}."""
    preset = await preset_svc.get_preset(db, identity, preset_id)
    return VmPresetResponse.model_validate(preset)


@router_presets.patch(
    "/{preset_id}",
    response_model=VmPresetResponse,
    summary="Изменить пресет стандартной ВМ (частично)",
    responses={
        403: {"description": "Нет `vm_preset_manage`."},
        404: {"description": "VM_PRESET_NOT_FOUND / чужой отдел."},
        409: {"description": "VM_PRESET_DUPLICATE."},
        422: {"description": "VM_PRESET_UPDATE_EMPTY."},
    },
)
async def update_preset(
    preset_id: str,
    body: VmPresetUpdate,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> VmPresetResponse:
    """PATCH /vm-presets/{id}."""
    preset = await preset_svc.update_preset(db, identity, request, preset_id, body)
    return VmPresetResponse.model_validate(preset)


@router_presets.delete(
    "/{preset_id}",
    status_code=204,
    summary="Удалить пресет стандартной ВМ",
    responses={
        403: {"description": "Нет `vm_preset_manage`."},
        404: {"description": "VM_PRESET_NOT_FOUND / чужой отдел."},
    },
)
async def delete_preset(
    preset_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> None:
    """DELETE /vm-presets/{id}."""
    await preset_svc.delete_preset(db, identity, preset_id)
