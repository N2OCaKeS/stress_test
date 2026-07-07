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
    VmPowerRequest,
    VmReserveRequest,
    VmResponse,
    VmsHubPrepareResponse,
    VmStatusUpdate,
    VmTaskDispatchResponse,
)
from src.services import vm as svc

router = APIRouter(prefix="/vms")
# prepare-vms-hub живёт под /servers/{id}; отдельный роутер, чтобы не тащить
# в servers.py VM-зависимости.
router_servers = APIRouter(prefix="/servers/{server_id}")


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
