"""Use cases для ВМ — права зоны vm, изоляция отделов, ёмкость hub'а, dispatch.

Владелец контракта VM-домена: модели/эндпоинты диспатчат воркеру задачи
`vms_hub.prepare` / `vm.create` / `vm.power` / `vm.delete` и принимают его
callback'и (`/internal/vms/{id}/state`, `/internal/servers/{id}/vms-hub-state`).
"""

import logging
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    Action,
    EntityType,
    PlatformRole,
    SERVICE_NAME,
    ServerStatus,
    ServiceRole,
    VM_POWER_ACTIONS,
    VM_STATUS_FREE,
    VmBusyState,
    VmPowerState,
    VmTaskKind,
)
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.idempotency import read_idempotency_key
from src.models import Vm
from src.repositories import server as server_repo
from src.repositories import server_disk as disk_repo
from src.repositories import vm as repo
from src.schemas.identity import IdentityContext
from src.schemas.vm import VmCreate
from src.services import audit_service, permissions, worker_client
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import vm_id as new_vm_id

logger = logging.getLogger(__name__)


# ── visibility / booking helpers ─────────────────────────────────────────────


async def _ensure_visible(
    db: AsyncSession, identity: IdentityContext, vm: Vm
) -> None:
    """Скрыть невидимую ВМ за 404 (свой отдел ИЛИ инстанс-грант на ВМ)."""
    if identity.department_id == vm.department_id:
        return
    if await permissions.has_resource_grant(db, identity, EntityType.VM, vm.id):
        return
    raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")


def _is_vm_admin(identity: IdentityContext, vm: Vm) -> bool:
    """True, если caller — админ по отношению к ВМ (booking-bypass).

    Админ = platform `department_admin` отдела ВМ ЛИБО носитель service-роли
    `admin` в server_service. Симметрия с `reservation.is_server_admin`.
    """
    if (
        identity.platform_role == PlatformRole.DEPARTMENT_ADMIN
        and identity.department_id is not None
        and identity.department_id == vm.department_id
    ):
        return True
    return ServiceRole.ADMIN in identity.roles_for_service(SERVICE_NAME)


def _ensure_bookable(identity: IdentityContext, vm: Vm, *, action: str) -> None:
    """Гейт брони: управлять ВМ можно, если она свободна / забронирована под себя
    / caller — админ. Иначе 409 VM_RESERVED. Симметрия с серверной бронью, но по
    полю `status` (§2 дизайна)."""
    if vm.status == VM_STATUS_FREE:
        return
    if vm.status == identity.username:
        return
    if _is_vm_admin(identity, vm):
        return
    audit_service.emit(
        "vm.reservation_denied",
        target_id=vm.id, target_type="vm",
        status="denied", allowed=False,
        details={"blocked_action": action, "status": vm.status, "department_id": vm.department_id},
    )
    raise ConflictError(
        error_code="VM_RESERVED",
        message=(
            "VM is reserved by another user; operations are limited to the "
            "reservation owner or a department/service admin"
        ),
        details={"status": vm.status},
    )


def _ensure_not_busy(vm: Vm, *, action: str) -> None:
    """Отбить операцию, пока идёт lifecycle-операция (busy_state непустой)."""
    if vm.busy_state is None:
        return
    audit_service.emit(
        "vm.busy_denied",
        target_id=vm.id, target_type="vm",
        status="denied", allowed=False,
        details={"blocked_action": action, "busy_state": vm.busy_state, "department_id": vm.department_id},
    )
    raise ConflictError(
        error_code="VM_BUSY",
        message=f"VM has an operation in progress ({vm.busy_state}); try again later",
        details={"busy_state": vm.busy_state},
    )


def _hub_payload(hub) -> dict:
    """Общие ключи адресации hub'а для VM-task'ов (SSH всегда по IP)."""
    return {
        "server_id": hub.id,
        "target_department_id": hub.department_id,
        # SSH-таргет — ВСЕГДА IP hub'а, не hostname (worker-под не резолвит
        # короткие имена).
        "host": str(hub.ip_address),
        "ssh_port": hub.ssh_port,
        "is_managed": hub.is_managed,
        "management_user": hub.management_user,
    }


# ── read ─────────────────────────────────────────────────────────────────────


async def list_vms(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    limit: int,
    offset: int,
) -> tuple[list[Vm], int]:
    """List + count ВМ. Тип-wide view → весь отдел; иначе grant-only листинг."""
    has_type_view = await permissions.has_action(
        db, identity, EntityType.VM, Action.VIEW
    )
    if has_type_view:
        if identity.department_id is None:
            return [], 0
        dept_filter = [identity.department_id]
        items = await repo.list_in_departments(db, dept_filter, limit=limit, offset=offset)
        total = await repo.count_in_departments(db, dept_filter)
        return items, total
    granted_ids = sorted(
        await permissions.granted_resource_ids(db, identity, EntityType.VM)
    )
    if not granted_ids:
        await permissions.require_action(db, identity, EntityType.VM, Action.VIEW)
    items = await repo.list_by_ids(db, granted_ids, limit=limit, offset=offset)
    total = await repo.count_by_ids(db, granted_ids)
    return items, total


async def get_vm(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
) -> Vm:
    """SELECT ВМ + permission (VIEW) + visibility-check (cross-dept → 404)."""
    try:
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VIEW
        )
        obj = await repo.get_by_id(db, vm_id)
        if obj is None:
            raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
        await _ensure_visible(db, identity, obj)
    except (NotFoundError, AuthorizationError) as exc:
        reason = (
            "not_found_or_cross_dept"
            if isinstance(exc, NotFoundError) else "permission_denied"
        )
        status = "failure" if isinstance(exc, NotFoundError) else "denied"
        audit_service.emit(
            "vm.view", target_id=vm_id, target_type="vm",
            status=status, allowed=isinstance(exc, NotFoundError),
            details={"reason": reason},
        )
        raise
    return obj


async def get_vm_by_number(
    db: AsyncSession,
    identity: IdentityContext,
    number: int,
) -> Vm:
    """SELECT ВМ по номеру + permission/visibility. Не найдено → 404 VM_NOT_FOUND."""
    obj = await repo.get_by_number(db, number)
    if obj is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    return await get_vm(db, identity, obj.id)


# ── create (capacity check + dispatch VM_CREATE) ─────────────────────────────


def _capacity_check(hub, existing: dict[str, int], payload: VmCreate) -> None:
    """Σ(vCPU/RAM/disk уже созданных ВМ) + запрос ≤ ресурсы hub'а → иначе 409.

    Проверяем только те измерения, чья ёмкость hub'а известна (из inventory);
    неизвестное измерение (None/0) не гейтим. Без оверкоммита.
    """
    hub_cpu = hub.cpu_threads or hub.cpu_cores
    hub_ram = hub.ram_total_mb
    hub_disk_gb = sum(d.size_gb or 0 for d in getattr(hub, "_hub_disks", []))
    checks = [
        ("cpu", hub_cpu, existing["cpu"] + payload.cpu),
        ("ram_mb", hub_ram, existing["ram_mb"] + payload.ram_mb),
        ("disk_gb", hub_disk_gb, existing["disk_gb"] + payload.disk_gb),
    ]
    for dim, capacity, requested in checks:
        if capacity and requested > capacity:
            raise ConflictError(
                error_code="VM_CAPACITY_EXCEEDED",
                message=(
                    f"Hub {dim} capacity exceeded: requested total {requested} "
                    f"> hub {capacity}"
                ),
                details={"dimension": dim, "hub_capacity": capacity, "requested_total": requested},
            )


async def create_vm(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    payload: VmCreate,
) -> tuple[Vm, str]:
    """INSERT ВМ (busy_state=creating) + dispatch VM_CREATE. Возвращает (vm, task_id).

    Права: `(vm, create)` (тип-wide, dept-scope). Ёмкость hub'а проверяется до
    dispatch'а (409 VM_CAPACITY_EXCEEDED). Hub обязан быть подготовлен как
    VMS-hub (`is_vms_hub`), иначе 409 HUB_NOT_PREPARED.
    """
    with emit_denied_on_authz_error(
        "vm.create", target_type="vm",
        extra_details={"department_id": payload.department_id, "hub_server_id": payload.hub_server_id},
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.VM, Action.CREATE)
    if payload.department_id != identity.department_id:
        audit_service.emit(
            "vm.create", target_type="vm", status="failure", allowed=True,
            details={"reason": "department_isolation", "department_id": payload.department_id},
        )
        raise AuthorizationError(
            error_code="DEPARTMENT_ISOLATION",
            message="Cannot create a VM in a different department",
        )
    hub = await server_repo.get_by_id(db, payload.hub_server_id)
    if hub is None or hub.department_id != identity.department_id:
        audit_service.emit(
            "vm.create", target_type="vm", status="failure", allowed=True,
            details={"reason": "hub_not_found_or_cross_dept", "hub_server_id": payload.hub_server_id},
        )
        raise NotFoundError(error_code="HUB_NOT_FOUND", message="Hub server not found")
    if not hub.is_vms_hub:
        audit_service.emit(
            "vm.create", target_type="vm", status="failure", allowed=True,
            details={"reason": "hub_not_prepared", "hub_server_id": hub.id},
        )
        raise ConflictError(
            error_code="HUB_NOT_PREPARED",
            message="Server is not prepared as a VMS-hub; run prepare-vms-hub first",
        )

    # Ёмкость: подгружаем диски hub'а и сумму ресурсов уже созданных ВМ.
    hub._hub_disks = await disk_repo.list_all_for_server(db, hub.id)
    existing = await repo.sum_resources_for_hub(db, hub.id)
    _capacity_check(hub, existing, payload)

    data = {
        "id": new_vm_id(),
        "name": payload.name,
        "number": payload.number,
        "hub_server_id": hub.id,
        "department_id": payload.department_id,
        "os_version": payload.os_version,
        "box": payload.box,
        "network_mode": payload.network_mode.value,
        "ip_address": str(payload.ip_address) if payload.ip_address is not None else None,
        "status": VM_STATUS_FREE,
        "power_state": VmPowerState.UNKNOWN.value,
        "cpu": payload.cpu,
        "ram_mb": payload.ram_mb,
        "disk_gb": payload.disk_gb,
        "autostart": payload.autostart,
        "cred_strategy": payload.cred_strategy.value,
        "busy_state": VmBusyState.CREATING.value,
        "busy_since": datetime.now(timezone.utc),
        "created_by": identity.user_id,
    }
    try:
        vm = await repo.create(db, data)
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "vm.create", target_id=data["id"], target_type="vm",
            status="failure", allowed=True,
            details={"reason": "duplicate", "department_id": payload.department_id},
        )
        raise ConflictError(
            error_code="VM_DUPLICATE",
            message="VM with this name (on the hub) or number already exists",
        ) from exc

    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "name": vm.name,
        "box": vm.box,
        "os_version": vm.os_version,
        "network_mode": vm.network_mode,
        "ip_address": data["ip_address"],
        "cpu": vm.cpu,
        "ram_mb": vm.ram_mb,
        "disk_gb": vm.disk_gb,
        "autostart": vm.autostart,
        "cred_strategy": vm.cred_strategy,
    }
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_CREATE, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.create",
    )
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.created", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "hub_server_id": hub.id, "name": vm.name, "department_id": vm.department_id},
    )
    return vm, task_id


async def _dispatch_vm_task(
    *,
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    task_kind: str,
    hub,
    vm: Vm | None,
    payload: dict,
    audit_action: str,
) -> str:
    """Тонкая обёртка над worker_client.dispatch_task с failure-аудитом.

    target_server_id — всегда hub (SSH-таргет), target_resource_id — id ВМ
    (когда она есть). commit делает caller (create) или сама операция (power/
    delete), чтобы outbox-row лёг в одну транзакцию с доменной мутацией.
    """
    from src.core.exceptions import ServiceUnavailableError

    try:
        return await worker_client.dispatch_task(
            db=db,
            task_kind=task_kind,
            target_server_id=hub.id,
            target_resource_id=vm.id if vm is not None else None,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=read_idempotency_key(request),
        )
    except (ConflictError, ServiceUnavailableError) as exc:
        reason = (
            "idempotent_conflict" if isinstance(exc, ConflictError) else "worker_unreachable"
        )
        audit_service.emit(
            audit_action,
            target_id=(vm.id if vm is not None else hub.id),
            target_type=("vm" if vm is not None else "server"),
            status="failure", allowed=True,
            details={"reason": reason, "task_kind": task_kind, "hub_server_id": hub.id},
        )
        raise


# ── power / delete ───────────────────────────────────────────────────────────


async def power_vm(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    action: str,
) -> tuple[Vm, str]:
    """Dispatch VM_POWER (start/shutdown/reboot/reset/destroy). Гейт брони+lock."""
    if action not in VM_POWER_ACTIONS:
        raise DomainValidationError(
            error_code="INVALID_VM_POWER_ACTION",
            message=f"action must be one of {sorted(VM_POWER_ACTIONS)}",
        )
    with emit_denied_on_authz_error(
        "vm.powered", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id, "action": action}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VM_POWER
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.powered", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_not_busy(vm, action="vm.power")
    _ensure_bookable(identity, vm, action="vm.power")

    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    payload = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "name": vm.name,
        "action": action,
    }
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_POWER, hub=hub, vm=vm,
        payload=payload, audit_action="vm.powered",
    )
    await db.commit()
    audit_service.emit(
        "vm.powered", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "action": action, "department_id": vm.department_id},
    )
    return vm, task_id


async def delete_vm(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
) -> tuple[Vm, str]:
    """Dispatch VM_DELETE + удалить запись ВМ. Гейт брони+lock.

    Волна 1: запись сносится сразу (симметрия старому rm-vms-hub), задача
    VM_DELETE чистит домен на гипервизоре. Возвращает (vm, task_id).
    """
    with emit_denied_on_authz_error(
        "vm.deleted", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.DELETE
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.deleted", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_not_busy(vm, action="vm.delete")
    _ensure_bookable(identity, vm, action="vm.delete")

    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None:
        raise ConflictError(error_code="HUB_UNAVAILABLE", message="Hub server is unavailable")
    payload = {**_hub_payload(hub), "vm_id": vm.id, "name": vm.name}
    dept = vm.department_id
    name = vm.name
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_DELETE, hub=hub, vm=vm,
        payload=payload, audit_action="vm.deleted",
    )
    await repo.delete(db, vm)
    await db.commit()
    audit_service.emit(
        "vm.deleted", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "name": name, "department_id": dept},
    )
    return vm, task_id


# ── booking (reserve / release / status) ─────────────────────────────────────


async def reserve_vm(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    status: str | None,
) -> Vm:
    """Забронировать ВМ (status → run test / debug test / свой логин)."""
    with emit_denied_on_authz_error(
        "vm.reserved", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VM_RESERVE
        )
    vm = await _load_visible(db, identity, vm_id, audit_action="vm.reserved")
    # Уже занята кем-то другим (и caller не админ) → 409.
    if vm.status != VM_STATUS_FREE and vm.status != identity.username and not _is_vm_admin(identity, vm):
        audit_service.emit(
            "vm.reserved", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "already_reserved", "status": vm.status},
        )
        raise ConflictError(
            error_code="VM_ALREADY_RESERVED",
            message="VM is already reserved by another user",
            details={"status": vm.status},
        )
    new_status = status if status is not None else identity.username
    vm.status = new_status
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.reserved", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={"status": new_status, "department_id": vm.department_id},
    )
    return vm


async def release_vm(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
) -> Vm:
    """Снять бронь ВМ (status → free). Разрешено владельцу брони или админу."""
    with emit_denied_on_authz_error(
        "vm.released", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VM_RELEASE
        )
    vm = await _load_visible(db, identity, vm_id, audit_action="vm.released")
    if vm.status != VM_STATUS_FREE and vm.status != identity.username and not _is_vm_admin(identity, vm):
        audit_service.emit(
            "vm.released", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "reserved_by_other", "status": vm.status},
        )
        raise ConflictError(
            error_code="VM_RESERVED",
            message="VM is reserved by another user; only the owner or an admin can release it",
            details={"status": vm.status},
        )
    vm.status = VM_STATUS_FREE
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.released", target_id=vm_id, target_type="vm",
        status="success", allowed=True, details={"department_id": vm.department_id},
    )
    return vm


async def set_status(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    status: str,
) -> Vm:
    """Прямое выставление booking-статуса (PATCH /vms/{id}/status).

    Гейтится `vm_reserve`; при попытке перебить чужую бронь (не free, не своя,
    не админ) → 409, кроме случая выставления `free` (это семантика release).
    """
    with emit_denied_on_authz_error(
        "vm.status_updated", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VM_RESERVE
        )
    vm = await _load_visible(db, identity, vm_id, audit_action="vm.status_updated")
    if (
        status != VM_STATUS_FREE
        and vm.status != VM_STATUS_FREE
        and vm.status != identity.username
        and not _is_vm_admin(identity, vm)
    ):
        audit_service.emit(
            "vm.status_updated", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "reserved_by_other", "status": vm.status},
        )
        raise ConflictError(
            error_code="VM_RESERVED",
            message="VM is reserved by another user",
            details={"status": vm.status},
        )
    vm.status = status
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.status_updated", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={"status": status, "department_id": vm.department_id},
    )
    return vm


async def _load_visible(
    db: AsyncSession, identity: IdentityContext, vm_id: str, *, audit_action: str,
) -> Vm:
    """Загрузить ВМ с visibility-check; failure-аудит на 404."""
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            audit_action, target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    return vm


# ── prepare-vms-hub (dispatch VMS_HUB_PREPARE, gate virtualization) ───────────


async def prepare_vms_hub(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    server_id: str,
) -> tuple[str, str]:
    """Dispatch VMS_HUB_PREPARE на сервер. Возвращает (server_id, task_id).

    Права: `(vm, vms_hub_prepare)` (тип-wide, dept-scope). Сервер обязан
    поддерживать виртуализацию (`virtualization=True`) и быть prepared
    (`is_managed`) — иначе 409.
    """
    with emit_denied_on_authz_error(
        "vms_hub.prepared", target_id=server_id, target_type="server",
        extra_details={"server_id": server_id}, identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.VM, Action.VMS_HUB_PREPARE)
    hub = await server_repo.get_by_id(db, server_id)
    if hub is None or hub.department_id != identity.department_id:
        audit_service.emit(
            "vms_hub.prepared", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    if not hub.is_managed:
        audit_service.emit(
            "vms_hub.prepared", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "prepare_required"},
        )
        raise ConflictError(
            error_code="PREPARE_REQUIRED",
            message="Server must be prepared for management before it can become a VMS-hub",
        )
    if not hub.virtualization:
        audit_service.emit(
            "vms_hub.prepared", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "virtualization_unsupported"},
        )
        raise ConflictError(
            error_code="VIRTUALIZATION_NOT_SUPPORTED",
            message="Server does not support hardware virtualization (KVM); cannot become a VMS-hub",
        )
    payload = {
        **_hub_payload(hub),
        "phy_if": hub.network_interface_name,
    }
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VMS_HUB_PREPARE, hub=hub, vm=None,
        payload=payload, audit_action="vms_hub.prepared",
    )
    await db.commit()
    audit_service.emit(
        "vms_hub.prepared", target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={"task_id": task_id, "department_id": hub.department_id},
    )
    return server_id, task_id
