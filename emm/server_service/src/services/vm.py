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
    VM_CONSOLE_KINDS,
    VM_GRAPHICS_CONSOLE_KINDS,
    VM_POWER_ACTIONS,
    VM_STATUS_FREE,
    VM_SYSTEM_SNAPSHOT_SUFFIX,
    VmBusyState,
    VmNetworkMode,
    VmPowerState,
    VmSnapshotKind,
    VmSnapshotState,
    VmTaskKind,
)
from src.core.constants import VmDiskState
from src.core.config import get_settings
from src.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.idempotency import read_idempotency_key
from src.models import Vm, VmDisk, VmSnapshot
from src.repositories import os_version as os_version_repo
from src.repositories import server_account as account_repo
from src.repositories import server as server_repo
from src.repositories import server_disk as disk_repo
from src.repositories import vm as repo
from src.repositories import vm_disk as vm_disk_repo
from src.repositories import vm_image as vm_image_repo
from src.repositories import vm_package_inventory as vm_package_repo
from src.repositories import vm_preset as vm_preset_repo
from src.repositories import vm_snapshot as vm_snapshot_repo
from src.schemas.identity import IdentityContext
from src.schemas.vm import (
    VmAlltaUpdateRequest,
    VmAstraUpdateRequest,
    VmBulkCreateResult,
    VmCreate,
    VmDiskCreate,
    VmIdentityUpdateRequest,
    VmNetworkRequest,
    VmPasswdRequest,
    VmSnapshotCreate,
    VmCredStrategyRequest,
    VmUpdateRequest,
)
from src.services import account_nopasswd_sudo_settings as nopasswd_sudo_svc
from src.services import audit_service, console_token, permissions, reservation, secrets_service, worker_client
from src.services import box_service
from src.services import vm_reservation
from src.services import management_user_config
from src.services import vm_ip_pool as ip_pool_svc
from src.services.management_creds import generate_management_material
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import dispatch_creds_id
from src.utils.ids import vm_console_token as new_console_token
from src.utils.ids import vm_disk_id as new_vm_disk_id
from src.utils.ids import vm_id as new_vm_id
from src.utils.ids import vm_snapshot_id as new_vm_snapshot_id

logger = logging.getLogger(__name__)

# Дефолты для teardown'а VMS-hub'а, которые forward'им воркеру: семейство
# пакетов (Astra — apt) и путь storage-pool'а с образами. Совпадают с тем, что
# воркер подставляет сам, но кладём явно, чтобы контракт задачи был полным.
VMS_HUB_OS_FAMILY = "apt"
VMS_HUB_POOL_PATH = "/vms"

# Sentinel: «взять Idempotency-Key из заголовка запроса». Отличает «ключ не
# передан явно» (single-dispatch — читаем header) от «явно без ключа» (bulk —
# каждый элемент диспатчится независимо, общий header-ключ дал бы дедуп).
_IDEMPOTENCY_FROM_REQUEST = object()


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
    полю `status`.

    Сервисная бронь `acs`/`testing` не пропускает никого, включая
    админа — `vm_reservation.ensure_not_service_locked`. Кроме консоли: она
    не меняет ВМ, а смотреть идущий тест админу нужно (как console-read у
    серверов вне гейта брони)."""
    if action != "vm.console":
        vm_reservation.ensure_not_service_locked(vm, action=action)
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
    """SELECT ВМ по номеру стенда своего отдела + permission. 404 VM_NOT_FOUND если нет.

    Номер уникален только в рамках department_id — lookup всегда идёт в
    department caller'а.
    """
    obj = await repo.get_by_department_number(db, identity.department_id, number)
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


async def _resolve_vm_accounts(
    db: AsyncSession,
    identity: IdentityContext,
    department_id: str,
    account_ids: list[str],
) -> list:
    """Валидировать и подгрузить server_account'ы для привязки к ВМ.

    Каждый аккаунт обязан существовать (иначе 404 ACCOUNT_NOT_FOUND) и жить в
    том же отделе, что и ВМ (иначе 403 VM_ACCOUNT_FORBIDDEN — dept-isolation
    учёток). Порядок исходного списка сохраняется, дубли схлопываются.
    """
    accounts = []
    seen: set[str] = set()
    for aid in account_ids:
        if aid in seen:
            continue
        seen.add(aid)
        account = await account_repo.get_by_id(db, aid)
        if account is None:
            audit_service.emit(
                "vm.create", target_type="vm", status="failure", allowed=True,
                details={"reason": "account_not_found", "account_id": aid},
            )
            raise NotFoundError(
                error_code="ACCOUNT_NOT_FOUND",
                message="Server account not found",
            )
        if account.department_id != department_id:
            audit_service.emit(
                "vm.create", target_type="vm", status="failure", allowed=True,
                details={"reason": "account_cross_dept", "account_id": aid},
            )
            raise AuthorizationError(
                error_code="VM_ACCOUNT_FORBIDDEN",
                message="Account belongs to a different department and cannot be attached to this VM",
            )
        accounts.append(account)
    return accounts


async def create_vm(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    payload: VmCreate,
    *,
    idempotency_key=_IDEMPOTENCY_FROM_REQUEST,
) -> tuple[Vm, str]:
    """INSERT ВМ (busy_state=creating) + dispatch VM_CREATE. Возвращает (vm, task_id).

    Права: `(vm, create)` (тип-wide, dept-scope). Ёмкость hub'а проверяется до
    dispatch'а (409 VM_CAPACITY_EXCEEDED). Hub обязан быть подготовлен как
    VMS-hub (`is_vms_hub`), иначе 409 HUB_NOT_PREPARED. `accounts` — существующие
    учётки отдела: привязываются к ВМ и уезжают в payload воркера для провижна.
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

    # Учётки для провижна в госте: существующие аккаунты того же отдела.
    accounts = await _resolve_vm_accounts(
        db, identity, payload.department_id, payload.accounts
    )
    # Один lookup на всю ВМ (все привязанные учётки делят department ВМ) —
    # sudo-аккаунты получат per-user NOPASSWD sudoers-правило только если
    # отдел явно включил `/settings/account-nopasswd-sudo`.
    dept_nopasswd_sudo_enabled = await nopasswd_sudo_svc.is_enabled_for_department(
        db, payload.department_id,
    )

    # Резолв box→box_url из каталога образов ДО INSERT'а: воркеру нужен URL,
    # откуда скачивать образ. Нет записи в каталоге → 400 (карточку не заводим).
    box_url, box_os_versions = await _resolve_box_url(db, payload.box, hub.id)

    # Бокс из реестра отдела: base_user-креды образа, его os_versions и
    # download_url едут воркеру. Пароль расшифровывается под AAD бокса, наружу не
    # отдаётся. Чужой/несуществующий бокс → 404 BOX_NOT_FOUND.
    box_fields = await _resolve_registry_box(db, identity, payload.box_id)

    # Bridge-ВМ нужен статический адрес: берём заданный ip_address (с проверкой
    # занятости) либо авто-выбираем свободный из пула. nat — адрес выдаёт libvirt,
    # ничего не резолвим. Без адреса и без пула bridge создать нельзя.
    resolved_ip: str | None = None
    net_pool = None
    if payload.network_mode == VmNetworkMode.BRIDGE:
        resolved_ip, net_pool = await ip_pool_svc.resolve_bridge_ip(
            db, identity,
            department_id=payload.department_id,
            ip_address=str(payload.ip_address) if payload.ip_address is not None else None,
            pool_id=payload.pool_id,
            audit_action="vm.create",
        )
        if resolved_ip is None:
            audit_service.emit(
                "vm.create", target_type="vm", status="failure", allowed=True,
                details={"reason": "bridge_ip_required", "department_id": payload.department_id},
            )
            raise BadRequestError(
                error_code="VM_BRIDGE_IP_REQUIRED",
                message=(
                    "Bridge VMs need a static IP: pass ip_address or a pool_id "
                    "with free addresses"
                ),
            )

    data = {
        "id": new_vm_id(),
        "name": payload.name,
        "hostname": payload.hostname,
        "number": payload.number,
        "hub_server_id": hub.id,
        "department_id": payload.department_id,
        "os_version": payload.os_version,
        "box": payload.box,
        "network_mode": payload.network_mode.value,
        "ip_address": resolved_ip,
        "status": VM_STATUS_FREE,
        "power_state": VmPowerState.UNKNOWN.value,
        "cpu": payload.cpu,
        "ram_mb": payload.ram_mb,
        "disk_gb": payload.disk_gb,
        "autostart": payload.autostart,
        "graphics": payload.graphics,
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

    # Привязка учёток к ВМ (зеркало серверной M2M). Занятый логин на ВМ →
    # IntegrityError на uq_vm_login → 409.
    try:
        if accounts:
            await account_repo.add_vm_links(db, vm.id, accounts)
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "vm.create", target_id=data["id"], target_type="vm",
            status="failure", allowed=True,
            details={"reason": "account_link_conflict", "department_id": payload.department_id},
        )
        raise ConflictError(
            error_code="VM_ACCOUNT_DUPLICATE",
            message="Two attached accounts share a login, or a login is already bound to this VM",
        ) from exc

    # prepare встроен в сборку: server_service генерит per-VM управляющую пару +
    # пароль, шифрует в модель (`mgmt_*`, `mgmt_creds_pending_apply=True`), а
    # plaintext кладёт в Redis-stash — в payload едет только `creds_stash_key`.
    # Воркер применяет материал на КАЖДУЮ версию сборки (заводит dbos-учётку,
    # сносит `u`) и подтверждает управляемость callback'ом `vms/{id}/prepared`.
    new_creds = _store_new_mgmt_creds(vm)
    mgmt_user = (await management_user_config.get_config(db)).login
    stash_key = await _stash_vm_mgmt_creds(
        vm,
        {
            "management_user": mgmt_user,
            "public_key": new_creds["public_key"],
            "private_key": new_creds["private_key"],
            "password": new_creds["password"],
        },
        audit_action="vm.create",
    )

    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "name": vm.name,
        "hostname": vm.hostname or vm.name,
        "box": vm.box,
        # download_url реестрового бокса — фолбэк, если box_url из vm_images-
        # каталога не резолвился.
        "box_url": box_url or box_fields.get("download_url"),
        "os_version": vm.os_version,
        # os_versions реестрового бокса перекрывают версии из vm_images-каталога.
        "os_versions": box_fields.get("os_versions") or box_os_versions,
        "network_mode": vm.network_mode,
        "ip_address": data["ip_address"],
        "gateway": str(net_pool.gateway) if net_pool is not None and net_pool.gateway is not None else None,
        "netmask": net_pool.netmask if net_pool is not None else None,
        "dns": list(net_pool.dns) if net_pool is not None and net_pool.dns is not None else None,
        "cpu": vm.cpu,
        "ram_mb": vm.ram_mb,
        "disk_gb": vm.disk_gb,
        "autostart": vm.autostart,
        "graphics": vm.graphics,
        "cred_strategy": vm.cred_strategy,
        "creds_stash_key": stash_key,
        "accounts": [
            {
                "account_id": a.id,
                "login": a.login,
                "has_sudo": a.has_sudo,
                "unix_groups": list(a.unix_groups or []),
                "ssh_public_key": a.ssh_public_key,
                "nopasswd_sudo": bool(a.has_sudo) and dept_nopasswd_sudo_enabled,
            }
            for a in accounts
        ],
    }
    # base_user-креды образа (login + plaintext пароль) — воркер провижнит base-
    # учётку бокса. Пароль в HTTP-ответ не попадает, только в dispatch-payload.
    if "base_user_login" in box_fields:
        payload_task["base_user_login"] = box_fields["base_user_login"]
    if "base_user_password" in box_fields:
        payload_task["base_user_password"] = box_fields["base_user_password"]
    try:
        task_id = await _dispatch_vm_task(
            db=db, identity=identity, request=request,
            task_kind=VmTaskKind.VM_CREATE, hub=hub, vm=vm,
            payload=payload_task, audit_action="vm.create",
            idempotency_key=idempotency_key,
        )
    except Exception:
        # Stash осиротел — задача в брокер не доехала, воркер за материалом не
        # пойдёт; чистим ключ, чтобы plaintext не висел до TTL.
        await worker_client.delete_dispatch_creds(stash_key)
        raise
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.created", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "hub_server_id": hub.id, "name": vm.name, "department_id": vm.department_id},
    )
    return vm, task_id


async def bulk_create_vms(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    items: list[VmCreate],
) -> list[VmBulkCreateResult]:
    """Создать несколько ВМ за один запрос — по задаче `vm.create` на элемент.

    Право `(vm, create)` проверяется один раз на весь батч (не зависит от
    конкретной ВМ) — нет права → 403 на весь запрос. Дальше каждый элемент
    создаётся независимо через `create_vm`: упавший уходит в результат с
    error_code/message, остальные продолжают. Ёмкость hub'а копится по мере
    коммита каждой созданной ВМ. Глобальная недоступность воркера (redis down)
    на любом элементе отбивает весь запрос 503.
    """
    from src.core.exceptions import AppException, ServiceUnavailableError

    with emit_denied_on_authz_error(
        "vm.create", target_type="vm",
        extra_details={"operation": "bulk_create", "count": len(items)},
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.VM, Action.CREATE)

    results: list[VmBulkCreateResult] = []
    for index, item in enumerate(items):
        try:
            # Bulk: без общего header-ключа — каждый элемент диспатчится
            # самостоятельно (общий Idempotency-Key дал бы дедуп на второй ВМ).
            vm, task_id = await create_vm(
                db, identity, request, item, idempotency_key=None,
            )
        except ServiceUnavailableError:
            # Воркер недоступен глобально — продолжать бессмысленно, каждый
            # следующий элемент упал бы идентично. Уже созданные закоммичены.
            audit_service.emit(
                "vm.create", target_type="vm", status="failure", allowed=True,
                details={
                    "reason": "worker_unreachable_bulk_abort",
                    "operation": "bulk_create",
                    "created_count": sum(1 for r in results if r.status == "created"),
                },
            )
            raise
        except AppException as exc:
            # Частичная мутация упавшего элемента (если была) — откатываем, чтобы
            # не тащить «грязную» транзакцию в следующий элемент.
            await db.rollback()
            results.append(VmBulkCreateResult(
                index=index, name=item.name, status="error",
                error_code=exc.error_code, message=exc.message,
            ))
            continue
        results.append(VmBulkCreateResult(
            index=index, name=item.name, status="created",
            vm_id=vm.id, task_id=task_id,
        ))

    created = sum(1 for r in results if r.status == "created")
    audit_service.emit(
        "vm.create", target_type="vm", status="success", allowed=True,
        details={
            "operation": "bulk_create",
            "created_count": created,
            "error_count": len(results) - created,
        },
    )
    return results


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
    idempotency_key=_IDEMPOTENCY_FROM_REQUEST,
) -> str:
    """Тонкая обёртка над worker_client.dispatch_task с failure-аудитом.

    target_server_id — всегда hub (SSH-таргет), target_resource_id — id ВМ
    (когда она есть). commit делает caller (create) или сама операция (power/
    delete), чтобы outbox-row лёг в одну транзакцию с доменной мутацией.

    `idempotency_key` по умолчанию читается из заголовка запроса; bulk передаёт
    None явно, чтобы каждый элемент диспатчился без общего header-ключа.
    """
    from src.core.exceptions import ServiceUnavailableError

    key = (
        read_idempotency_key(request)
        if idempotency_key is _IDEMPOTENCY_FROM_REQUEST
        else idempotency_key
    )
    try:
        return await worker_client.dispatch_task(
            db=db,
            task_kind=task_kind,
            target_server_id=hub.id,
            target_resource_id=vm.id if vm is not None else None,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=key,
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


async def _dispatch_guest_task_with_stash(
    *,
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    task_kind: str,
    hub,
    vm: Vm,
    payload: dict,
    audit_action: str,
    stash_key: str | None,
) -> str:
    """Dispatch guest-задачи ВМ с очисткой осиротевшего mgmt-stash'а при провале.

    Тонкая обёртка над `_dispatch_vm_task`: если задача в брокер не доехала,
    воркер за управляющим материалом не пойдёт — чистим ключ, чтобы plaintext
    не висел в Redis до TTL. `stash_key=None` (не-managed ВМ) — чистить нечего.
    """
    try:
        return await _dispatch_vm_task(
            db=db, identity=identity, request=request,
            task_kind=task_kind, hub=hub, vm=vm,
            payload=payload, audit_action=audit_action,
        )
    except Exception:
        if stash_key:
            await worker_client.delete_dispatch_creds(stash_key)
        raise


async def _resolve_box_url(
    db: AsyncSession, box: str | None, hub_id: str
) -> tuple[str | None, list[str]]:
    """Резолв box→(url, os_versions) из каталога `vm_images`.

    box=None → `(None, [])`. Сначала ищем hub-специфичную запись, потом
    глобальную. Нет записи (каталог пуст либо бокс не синкнут) → 400
    VM_BOX_NOT_IN_CATALOG — воркер без URL образ не скачает. `os_versions`
    (для universal-бокса) едет воркеру, чтобы он собрал снимки по версиям.
    """
    if not box:
        return None, []
    image = await vm_image_repo.resolve(db, box, hub_id)
    if image is None:
        audit_service.emit(
            "vm.create", target_type="vm", status="failure", allowed=True,
            details={"reason": "box_not_in_catalog", "box": box},
        )
        raise BadRequestError(
            error_code="VM_BOX_NOT_IN_CATALOG",
            message=(
                f"Box image '{box}' is not in the catalog; refresh the image "
                "catalog (POST /vm-images/refresh) or register the box first"
            ),
            details={"box": box},
        )
    return image.url, list(image.os_versions or [])


async def _resolve_registry_box(
    db: AsyncSession, identity: IdentityContext, box_id: str | None,
) -> dict:
    """Резолв реестрового бокса в фрагмент dispatch-payload (или `{}`).

    `box_id=None` → `{}` (прежнее поведение). Иначе тянем бокс своего отдела
    через box_service (чужой/несуществующий → 404 BOX_NOT_FOUND) и получаем
    base_user-креды образа + os_versions + download_url для payload'а воркера.
    """
    if box_id is None:
        return {}
    try:
        return await box_service.resolve_box_for_dispatch(db, identity, box_id)
    except NotFoundError:
        audit_service.emit(
            "vm.create", target_type="vm", status="failure", allowed=True,
            details={"reason": "box_not_found_or_cross_dept", "box_id": box_id},
        )
        raise


# ── update (cpu/ram) ─────────────────────────────────────────────────────────


def _capacity_check_update(hub, existing: dict[str, int], vm: Vm, new: dict) -> None:
    """Ёмкость при изменении ресурсов ВМ: Σ(hub) − текущее ВМ + запрошенное ≤ hub.

    `existing` включает саму ВМ, поэтому её текущий вклад вычитаем перед
    добавлением нового значения. Проверяем только измерения из `new`.
    """
    hub_cpu = hub.cpu_threads or hub.cpu_cores
    hub_ram = hub.ram_total_mb
    dims = {
        "cpu": (hub_cpu, vm.cpu or 0),
        "ram_mb": (hub_ram, vm.ram_mb or 0),
    }
    for dim, requested in new.items():
        capacity, own_current = dims[dim]
        total = existing[dim] - own_current + requested
        if capacity and total > capacity:
            raise ConflictError(
                error_code="VM_CAPACITY_EXCEEDED",
                message=(
                    f"Hub {dim} capacity exceeded: requested total {total} "
                    f"> hub {capacity}"
                ),
                details={"dimension": dim, "hub_capacity": capacity, "requested_total": total},
            )


async def update_vm_identity(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: VmIdentityUpdateRequest,
) -> Vm:
    """Синхронно сменить `name`/`number` карточки ВМ. Пустой диф → без UPDATE.

    Право `(vm, update)`. Ничего не применяется на hub'е/госте — только
    строка `vms`. UNIQUE-конфликт (`name` в пределах hub'а, `number` в
    рамках отдела) → 409 `VM_DUPLICATE`. `number` нельзя сбросить в null —
    отбивается на уровне схемы (`VmIdentityUpdateRequest`).
    """
    with emit_denied_on_authz_error(
        "vm.updated", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.UPDATE
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return vm
    for key, value in changes.items():
        setattr(vm, key, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "vm.updated", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="VM_DUPLICATE",
            message="VM with this name (on the hub) or number already exists",
        ) from exc
    await db.refresh(vm)
    audit_service.emit(
        "vm.updated", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"fields": list(changes.keys()), "department_id": vm.department_id},
    )
    return vm


async def set_cred_strategy(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    payload: VmCredStrategyRequest,
) -> Vm:
    """Синхронно сменить режим mgmt-кред ВМ (per_snapshot/reroll).

    Право `(vm, update)`. Метаданные — без задачи воркеру.
    """
    with emit_denied_on_authz_error(
        "vm.updated", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.UPDATE
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    vm.cred_strategy = payload.cred_strategy.value
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.updated", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"field": "cred_strategy", "cred_strategy": vm.cred_strategy,
                 "department_id": vm.department_id},
    )
    return vm


async def update_vm(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    payload: VmUpdateRequest,
) -> tuple[Vm, str]:
    """Dispatch VM_UPDATE (cpu/ram). Гейт брони+lock, ёмкость при увеличении.

    Право `(vm, update)`. Пустое тело → 422. Меняем только присланные cpu/ram_mb;
    воркер делает stop→правка XML→start. Ставит busy_state=updating.
    """
    changes: dict = {}
    if payload.cpu is not None:
        changes["cpu"] = payload.cpu
    if payload.ram_mb is not None:
        changes["ram_mb"] = payload.ram_mb
    if not changes:
        raise DomainValidationError(
            error_code="VM_UPDATE_EMPTY",
            message="provide at least one of cpu / ram_mb to update",
        )
    with emit_denied_on_authz_error(
        "vm.updated", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.UPDATE
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.updated", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_not_busy(vm, action="vm.update")
    _ensure_bookable(identity, vm, action="vm.update")

    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    # Ёмкость только при увеличении измерения; уменьшение всегда проходит.
    increasing = {
        dim: val for dim, val in changes.items()
        if val > (getattr(vm, dim) or 0)
    }
    if increasing:
        existing = await repo.sum_resources_for_hub(db, hub.id)
        _capacity_check_update(hub, existing, vm, increasing)

    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "name": vm.name,
        "cpu": changes.get("cpu", vm.cpu),
        "ram_mb": changes.get("ram_mb", vm.ram_mb),
    }
    vm.busy_state = VmBusyState.UPDATING.value
    vm.busy_since = datetime.now(timezone.utc)
    for dim, val in changes.items():
        setattr(vm, dim, val)
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_UPDATE, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.updated",
    )
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.updated", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "changed": sorted(changes.keys()), "department_id": vm.department_id},
    )
    return vm, task_id


# ── disks (create / list / delete / resize) ──────────────────────────────────


def _disk_serial(vm: Vm, disk_name: str) -> str:
    """Serial устройства для attach'а: `<vm>_<disk>`."""
    return f"{vm.name}_{disk_name}"


async def _load_vm_for_disk_op(
    db: AsyncSession, identity: IdentityContext, vm_id: str, *, action: str,
) -> tuple[Vm, object]:
    """Общий пролог disk-операций: право vm_disk_manage + видимость + бронь/lock + hub."""
    with emit_denied_on_authz_error(
        "vm.disk_managed", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id, "op": action}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VM_DISK_MANAGE
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.disk_managed", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept", "op": action},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_not_busy(vm, action=f"vm.{action}")
    _ensure_bookable(identity, vm, action=f"vm.{action}")
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    return vm, hub


async def list_disks(
    db: AsyncSession, identity: IdentityContext, vm_id: str,
) -> list[VmDisk]:
    """Список дисков ВМ. Право `(vm, view)` + видимость (cross-dept → 404)."""
    await permissions.require_resource_action(
        db, identity, EntityType.VM, vm_id, Action.VIEW
    )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    return await vm_disk_repo.list_for_vm(db, vm_id)


async def create_disk(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    payload: VmDiskCreate,
) -> tuple[VmDisk, str]:
    """INSERT диска (state=creating) + dispatch VM_DISK_ATTACH. Право `vm_disk_manage`."""
    vm, hub = await _load_vm_for_disk_op(db, identity, vm_id, action="disk_attach")
    disk_data = {
        "id": new_vm_disk_id(),
        "vm_id": vm.id,
        "name": payload.name,
        "size_gb": payload.size_gb,
        "target_dev": payload.target_dev,
        "serial": _disk_serial(vm, payload.name),
        "is_system": False,
        "fs": payload.fs,
        "mount": payload.mount,
        "state": VmDiskState.CREATING.value,
    }
    try:
        disk = await vm_disk_repo.create(db, disk_data)
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "vm.disk_managed", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "duplicate", "op": "disk_attach", "name": payload.name},
        )
        raise ConflictError(
            error_code="VM_DISK_DUPLICATE",
            message="A disk with this name already exists on the VM",
        ) from exc
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "disk_id": disk.id,
        "disk_name": disk.name,
        "size_gb": disk.size_gb,
        "target_dev": disk.target_dev,
        "serial": disk.serial,
        "fs": disk.fs,
        "mount": disk.mount,
    }
    # Guest-часть (mkfs/mount) идёт только при заданной ФС; managed-ВМ туда
    # ходит по управляющему ключу — расшифровываем и стэшим mgmt-материал.
    stash_key = None
    if disk.fs:
        stash_key = await stash_existing_vm_mgmt_creds(vm, "vm.disk_managed")
        if stash_key:
            payload_task["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_DISK_ATTACH, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.disk_managed", stash_key=stash_key,
    )
    await db.commit()
    await db.refresh(disk)
    audit_service.emit(
        "vm.disk_managed", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "op": "disk_attach", "disk_id": disk.id, "name": disk.name, "department_id": vm.department_id},
    )
    return disk, task_id


async def delete_disk(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    disk_id: str,
) -> tuple[VmDisk, str]:
    """Dispatch VM_DISK_DELETE + удалить строку диска. Право `vm_disk_manage`."""
    vm, hub = await _load_vm_for_disk_op(db, identity, vm_id, action="disk_delete")
    disk = await vm_disk_repo.get_by_id(db, disk_id)
    if disk is None or disk.vm_id != vm.id:
        audit_service.emit(
            "vm.disk_managed", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "disk_not_found", "op": "disk_delete", "disk_id": disk_id},
        )
        raise NotFoundError(error_code="VM_DISK_NOT_FOUND", message="Disk not found on this VM")
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "disk_id": disk.id,
        "disk_name": disk.name,
        "target_dev": disk.target_dev,
        "serial": disk.serial,
        "path": disk.path,
    }
    name = disk.name
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_DISK_DELETE, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.disk_managed",
    )
    await vm_disk_repo.delete(db, disk)
    await db.commit()
    audit_service.emit(
        "vm.disk_managed", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "op": "disk_delete", "disk_id": disk_id, "name": name, "department_id": vm.department_id},
    )
    return disk, task_id


async def resize_disk(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    disk_id: str,
    size_gb: int,
) -> tuple[VmDisk, str]:
    """Dispatch VM_DISK_RESIZE (qemu-img resize + growpart в госте). Только увеличение."""
    vm, hub = await _load_vm_for_disk_op(db, identity, vm_id, action="disk_resize")
    disk = await vm_disk_repo.get_by_id(db, disk_id)
    if disk is None or disk.vm_id != vm.id:
        audit_service.emit(
            "vm.disk_managed", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "disk_not_found", "op": "disk_resize", "disk_id": disk_id},
        )
        raise NotFoundError(error_code="VM_DISK_NOT_FOUND", message="Disk not found on this VM")
    if size_gb <= disk.size_gb:
        raise DomainValidationError(
            error_code="VM_DISK_SHRINK_FORBIDDEN",
            message=f"disk can only grow: new size {size_gb} must exceed current {disk.size_gb}",
            details={"current_gb": disk.size_gb, "requested_gb": size_gb},
        )
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "disk_id": disk.id,
        "disk_name": disk.name,
        "target_dev": disk.target_dev,
        "serial": disk.serial,
        "path": disk.path,
        "size_gb": size_gb,
    }
    disk.size_gb = size_gb
    disk.state = VmDiskState.CREATING.value
    # growpart/resize2fs исполняются в госте — managed-ВМ по управляющему ключу.
    stash_key = await stash_existing_vm_mgmt_creds(vm, "vm.disk_managed")
    if stash_key:
        payload_task["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_DISK_RESIZE, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.disk_managed", stash_key=stash_key,
    )
    await db.commit()
    await db.refresh(disk)
    audit_service.emit(
        "vm.disk_managed", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "op": "disk_resize", "disk_id": disk.id, "size_gb": size_gb, "department_id": vm.department_id},
    )
    return disk, task_id


# ── snapshots (create / list / revert / delete) ──────────────────────────────


async def _load_vm_for_managed_op(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    *,
    action_perm: str,
    audit_action: str,
    op: str,
) -> tuple[Vm, object]:
    """Общий пролог управляющих VM-операций: право + видимость + бронь/lock + hub.

    `action_perm` — action матрицы (vm_snapshot_manage / vm_astra_update / …),
    `audit_action` — action-key для denied/failure-аудита.
    """
    with emit_denied_on_authz_error(
        audit_action, target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id, "op": op}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, action_perm
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            audit_action, target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept", "op": op},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_not_busy(vm, action=f"vm.{op}")
    _ensure_bookable(identity, vm, action=f"vm.{op}")
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    return vm, hub


def _is_system_snapshot_name(name: str) -> bool:
    """True для имён системных golden-снимков (`<ver>_build`)."""
    return name.endswith(VM_SYSTEM_SNAPSHOT_SUFFIX)


def _copy_snapshot_creds(
    src: VmSnapshot | None, dst_id: str
) -> dict[str, str | None]:
    """Перешифровать mgmt-креды текущего снимка под AAD нового (режим per_snapshot).

    Контент нового снимка идентичен текущему, поэтому креды наследуются. Ключ
    есть только у server_service — decrypt старого AAD + encrypt под новым.
    Нет текущего снимка / кред → пустой набор (первый снимок ВМ).
    """
    if src is None or src.mgmt_password_encrypted is None:
        return {"mgmt_user": None, "mgmt_password_encrypted": None,
                "mgmt_ssh_private_key_encrypted": None}
    out: dict[str, str | None] = {"mgmt_user": src.mgmt_user}
    plain = secrets_service.decrypt(
        src.mgmt_password_encrypted,
        aad=secrets_service.aad_for_vm_snapshot_password(src.id),
    )
    out["mgmt_password_encrypted"] = secrets_service.encrypt(
        plain, aad=secrets_service.aad_for_vm_snapshot_password(dst_id)
    )
    if src.mgmt_ssh_private_key_encrypted is not None:
        key_plain = secrets_service.decrypt(
            src.mgmt_ssh_private_key_encrypted,
            aad=secrets_service.aad_for_vm_snapshot_ssh_key(src.id),
        )
        out["mgmt_ssh_private_key_encrypted"] = secrets_service.encrypt(
            key_plain, aad=secrets_service.aad_for_vm_snapshot_ssh_key(dst_id)
        )
    else:
        out["mgmt_ssh_private_key_encrypted"] = None
    return out


async def list_snapshots(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    *,
    q: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[VmSnapshot]:
    """Список снимков ВМ. Право `(vm, view)`.

    Показываются обе группы (`os_baseline` и `user`); скрыты только системные
    golden-снимки `<ver>_build` (`is_system`). `q` — substr-поиск по имени,
    `limit`/`offset` — постраничная выдача для скролла.
    """
    await permissions.require_resource_action(
        db, identity, EntityType.VM, vm_id, Action.VIEW
    )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    return await vm_snapshot_repo.list_for_vm(
        db, vm_id, include_system=False, q=q, limit=limit, offset=offset,
    )


async def create_snapshot(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    payload: VmSnapshotCreate,
) -> tuple[VmSnapshot, str]:
    """INSERT снимка (state=creating) + dispatch VM_SNAPSHOT_CREATE.

    Право `(vm, vm_snapshot_manage)`. Имя с суффиксом `_build` руками завести
    нельзя (403 — зарезервировано под системные). Дубль имени → 409. В режиме
    per_snapshot новый снимок наследует mgmt-креды текущего снимка (перешифровка).
    """
    vm, hub = await _load_vm_for_managed_op(
        db, identity, vm_id,
        action_perm=Action.VM_SNAPSHOT_MANAGE,
        audit_action="vm.snapshot_created", op="snapshot_create",
    )
    if _is_system_snapshot_name(payload.name):
        audit_service.emit(
            "vm.snapshot_created", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "system_snapshot_protected", "name": payload.name},
        )
        raise AuthorizationError(
            error_code="VM_SNAPSHOT_SYSTEM_PROTECTED",
            message="Snapshot names ending with _build are reserved for system golden snapshots",
        )
    snapshot_id = new_vm_snapshot_id()
    creds = {"mgmt_user": None, "mgmt_password_encrypted": None,
             "mgmt_ssh_private_key_encrypted": None}
    if vm.cred_strategy == "per_snapshot":
        current = await vm_snapshot_repo.get_current(db, vm.id)
        creds = _copy_snapshot_creds(current, snapshot_id)
    parent = await vm_snapshot_repo.get_current(db, vm.id)
    data = {
        "id": snapshot_id,
        "vm_id": vm.id,
        "name": payload.name,
        "description": payload.description,
        "parent_snapshot_id": parent.id if parent is not None else None,
        "snapshot_type": payload.snapshot_type.value,
        # Снимки, снятые пользователем, — всегда группа `user`. Чистые
        # os_baseline заводит сборочный флоу (sync-callback воркера).
        "kind": VmSnapshotKind.USER.value,
        "os_version": vm.os_version,
        "mode": None,
        "is_system": False,
        "state": VmSnapshotState.CREATING.value,
        "is_current": False,
        "created_by": identity.user_id,
        **creds,
    }
    try:
        snapshot = await vm_snapshot_repo.create(db, data)
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "vm.snapshot_created", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "duplicate", "name": payload.name},
        )
        raise ConflictError(
            error_code="VM_SNAPSHOT_EXISTS",
            message="A snapshot with this name already exists on the VM",
        ) from exc
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "snapshot_id": snapshot.id,
        "snapshot_name": snapshot.name,
        "snapshot_type": snapshot.snapshot_type,
        "description": snapshot.description,
        "parent_snapshot_name": parent.name if parent is not None else None,
    }
    vm.busy_state = VmBusyState.SNAPSHOTTING.value
    vm.busy_since = datetime.now(timezone.utc)
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_SNAPSHOT_CREATE, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.snapshot_created",
    )
    await db.commit()
    await db.refresh(snapshot)
    audit_service.emit(
        "vm.snapshot_created", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "snapshot_id": snapshot.id, "name": snapshot.name, "department_id": vm.department_id},
    )
    return snapshot, task_id


async def _load_snapshot_for_op(
    db: AsyncSession, vm: Vm, snapshot_id: str, *, audit_action: str, op: str,
    baseline_immutable: bool = True,
) -> VmSnapshot:
    """Загрузить снимок, проверить принадлежность ВМ и защиту эталонных снимков.

    Системные golden `<ver>_build` (`is_system`) заблокированы для любых ручных
    операций. Чистые эталоны версии ОС (`kind == os_baseline`, публичные
    `<ver>_orel`/`<ver>_smolensk`) защищены от изменения и удаления, но откат на
    них разрешён — при `baseline_immutable=False` (revert) проверка kind не
    выполняется. Пользовательские снимки (`kind == user`) — без ограничений.
    """
    snapshot = await vm_snapshot_repo.get_by_id(db, snapshot_id)
    if snapshot is None or snapshot.vm_id != vm.id:
        audit_service.emit(
            audit_action, target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "snapshot_not_found", "op": op, "snapshot_id": snapshot_id},
        )
        raise NotFoundError(
            error_code="VM_SNAPSHOT_NOT_FOUND",
            message="Snapshot not found on this VM",
        )
    if snapshot.is_system:
        audit_service.emit(
            audit_action, target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "system_snapshot_protected", "op": op, "snapshot_id": snapshot_id},
        )
        raise AuthorizationError(
            error_code="VM_SNAPSHOT_SYSTEM_PROTECTED",
            message="System golden snapshots (_build) cannot be reverted or deleted manually",
        )
    if baseline_immutable and snapshot.kind == VmSnapshotKind.OS_BASELINE.value:
        audit_service.emit(
            audit_action, target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "baseline_snapshot_protected", "op": op, "snapshot_id": snapshot_id},
        )
        raise AuthorizationError(
            error_code="VM_SNAPSHOT_BASELINE_PROTECTED",
            message="OS baseline snapshots cannot be modified or deleted manually",
        )
    return snapshot


async def revert_snapshot(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    snapshot_id: str,
) -> tuple[VmSnapshot, str]:
    """Dispatch VM_SNAPSHOT_REVERT. Право `(vm, vm_snapshot_manage)`.

    Системные `<ver>_build` откатывать руками нельзя (403). Эталоны версии ОС
    (`os_baseline`) откатывать можно — откат не мутирует сам снимок. В режиме
    per_snapshot активные креды ВМ переключаются на «снимковые» — переключение
    подтверждает callback `POST /internal/vms/{id}/snapshots` (is_current=true).
    """
    vm, hub = await _load_vm_for_managed_op(
        db, identity, vm_id,
        action_perm=Action.VM_SNAPSHOT_MANAGE,
        audit_action="vm.snapshot_reverted", op="snapshot_revert",
    )
    snapshot = await _load_snapshot_for_op(
        db, vm, snapshot_id, audit_action="vm.snapshot_reverted", op="snapshot_revert",
        baseline_immutable=False,
    )
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "snapshot_id": snapshot.id,
        "snapshot_name": snapshot.name,
        "cred_strategy": vm.cred_strategy,
    }
    vm.busy_state = VmBusyState.REVERTING.value
    vm.busy_since = datetime.now(timezone.utc)
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_SNAPSHOT_REVERT, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.snapshot_reverted",
    )
    await db.commit()
    await db.refresh(snapshot)
    audit_service.emit(
        "vm.snapshot_reverted", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "snapshot_id": snapshot.id, "name": snapshot.name, "cred_strategy": vm.cred_strategy, "department_id": vm.department_id},
    )
    return snapshot, task_id


async def delete_snapshot(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    snapshot_id: str,
) -> tuple[VmSnapshot, str]:
    """Dispatch VM_SNAPSHOT_DELETE + удалить строку снимка. Право `vm_snapshot_manage`.

    Системные `<ver>_build` и эталоны версии ОС (`os_baseline`) удалять руками
    нельзя (403). Пользовательские снимки — без ограничений.
    """
    vm, hub = await _load_vm_for_managed_op(
        db, identity, vm_id,
        action_perm=Action.VM_SNAPSHOT_MANAGE,
        audit_action="vm.snapshot_deleted", op="snapshot_delete",
    )
    snapshot = await _load_snapshot_for_op(
        db, vm, snapshot_id, audit_action="vm.snapshot_deleted", op="snapshot_delete",
    )
    name = snapshot.name
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "snapshot_id": snapshot.id,
        "snapshot_name": snapshot.name,
    }
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_SNAPSHOT_DELETE, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.snapshot_deleted",
    )
    await vm_snapshot_repo.delete(db, snapshot)
    await db.commit()
    audit_service.emit(
        "vm.snapshot_deleted", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "snapshot_id": snapshot_id, "name": name, "department_id": vm.department_id},
    )
    return snapshot, task_id


# ── astra-update / allta-update / passwd ─────────────────────────────────────


def _base_snapshot_family(rc: str) -> str:
    """Семейство базового golden-снимка по RC (major.minor, напр. 1.7.5.6 → 1.7).

    Воркер по семейству находит конкретный `<ver>_build` (1.7*→1.7.5.9_build,
    1.8*→1.8.1.6_build) — точная привязка golden'а живёт на hub'е.
    """
    parts = rc.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else rc


async def astra_update(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    payload: VmAstraUpdateRequest,
) -> tuple[Vm, str]:
    """Dispatch VM_ASTRA_UPDATE (revert <ver>_build → repo → astra-update → снимок rc).

    Право `(vm, vm_astra_update)`. Снимок с именем `rc` не должен существовать
    (409). repository_urls берутся из зарегистрированной OS-версии `rc`
    (404, если версия не заведена). Ставит busy_state=updating.
    """
    vm, hub = await _load_vm_for_managed_op(
        db, identity, vm_id,
        action_perm=Action.VM_ASTRA_UPDATE,
        audit_action="vm.astra_updated", op="astra_update",
    )
    existing = await vm_snapshot_repo.get_by_name(db, vm.id, payload.rc)
    if existing is not None:
        audit_service.emit(
            "vm.astra_updated", target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "snapshot_exists", "rc": payload.rc},
        )
        raise ConflictError(
            error_code="VM_SNAPSHOT_EXISTS",
            message=f"A snapshot named '{payload.rc}' already exists; RC already applied",
            details={"rc": payload.rc},
        )
    osv = await os_version_repo.get_by_name(db, payload.rc)
    if osv is None:
        audit_service.emit(
            "vm.astra_updated", target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "os_version_not_registered", "rc": payload.rc},
        )
        raise NotFoundError(
            error_code="OS_VERSION_NOT_FOUND",
            message=f"OS version '{payload.rc}' is not registered; add it first to resolve repositories",
            details={"rc": payload.rc},
        )
    strategy = payload.cred_strategy.value if payload.cred_strategy is not None else vm.cred_strategy
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "rc": payload.rc,
        "repository_urls": list(osv.repositories or []),
        "base_snapshot_family": _base_snapshot_family(payload.rc),
        "target_snapshot": payload.rc,
        "password": payload.password,
        "cred_strategy": strategy,
    }
    vm.busy_state = VmBusyState.UPDATING.value
    vm.busy_since = datetime.now(timezone.utc)
    # Guest-часть (перезапись sources.list, astra-update, смена пароля, перевод в
    # Смоленск) на managed-ВМ идёт по управляющему ключу — стэшим mgmt-материал.
    stash_key = await stash_existing_vm_mgmt_creds(vm, "vm.astra_updated")
    if stash_key:
        payload_task["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_ASTRA_UPDATE, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.astra_updated", stash_key=stash_key,
    )
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.astra_updated", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "rc": payload.rc, "repositories_count": len(osv.repositories or []), "department_id": vm.department_id},
    )
    return vm, task_id


async def _rotate_guest(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    *,
    task_kind: str,
    audit_action: str,
    op: str,
    action_perm: str,
    password: str | None,
    cred_strategy_override: str | None,
) -> tuple[Vm, str]:
    """Общий флоу allta-update / passwd: обновить гостевую allta + опц. пароль `u`.

    Идентичный op. В reroll воркер проходит по всем не-`_build`
    снимкам (revert→update→resnapshot), в per_snapshot трогает только текущий.
    server_service передаёт список не-системных снимков и стратегию.
    """
    vm, hub = await _load_vm_for_managed_op(
        db, identity, vm_id,
        action_perm=action_perm, audit_action=audit_action, op=op,
    )
    strategy = cred_strategy_override if cred_strategy_override is not None else vm.cred_strategy
    snapshots = await vm_snapshot_repo.list_for_vm(db, vm.id, include_system=False)
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "password": password,
        "cred_strategy": strategy,
        "snapshots": [
            {"snapshot_id": s.id, "name": s.name} for s in snapshots
        ],
    }
    vm.busy_state = VmBusyState.UPDATING.value
    vm.busy_since = datetime.now(timezone.utc)
    # Guest-часть reroll'а (обновление guest-allta, опц. смена пароля) на
    # managed-ВМ идёт по управляющему ключу — стэшим mgmt-материал.
    stash_key = await stash_existing_vm_mgmt_creds(vm, audit_action)
    if stash_key:
        payload_task["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=task_kind, hub=hub, vm=vm,
        payload=payload_task, audit_action=audit_action, stash_key=stash_key,
    )
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        audit_action, target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "cred_strategy": strategy, "password_changed": password is not None, "department_id": vm.department_id},
    )
    return vm, task_id


async def allta_update(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    payload: VmAlltaUpdateRequest,
) -> tuple[Vm, str]:
    """Dispatch VM_ALLTA_UPDATE. Право `(vm, vm_allta_update)`. Пароль опционален."""
    return await _rotate_guest(
        db, identity, request, vm_id,
        task_kind=VmTaskKind.VM_ALLTA_UPDATE,
        audit_action="vm.allta_updated", op="allta_update",
        action_perm=Action.VM_ALLTA_UPDATE,
        password=payload.password,
        cred_strategy_override=payload.cred_strategy.value if payload.cred_strategy is not None else None,
    )


async def passwd(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    payload: VmPasswdRequest,
) -> tuple[Vm, str]:
    """Dispatch VM_PASSWD (тот же op, пароль обязателен). Право `(vm, vm_passwd)`."""
    return await _rotate_guest(
        db, identity, request, vm_id,
        task_kind=VmTaskKind.VM_PASSWD,
        audit_action="vm.passwd_changed", op="passwd",
        action_perm=Action.VM_PASSWD,
        password=payload.password,
        cred_strategy_override=payload.cred_strategy.value if payload.cred_strategy is not None else None,
    )


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
        # worker поднимает natbr0 перед стартом NAT-ВМ — режим нужен ему в payload
        "network_mode": vm.network_mode,
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

    Запись сносится сразу (симметрия старому rm-vms-hub), задача
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
    payload = {**_hub_payload(hub), "vm_id": vm.id, "vm_name": vm.name}
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


async def delete_vms_for_clean(
    db: AsyncSession, hub, *, request_id: str | None = None,
) -> dict:
    """Снести все ВМ хаба при clean после переустановки ОС.

    Переустановка ОС на хабе физически стирает qcow2-диски всех его ВМ, так что
    записи становятся orphan'ами. Удаляем строки каскадом (`repo.delete` → FK
    ON DELETE CASCADE по vm_disks / vm_snapshots / vm_package_inventory /
    server_account_vms) без диспатча на хаб — домены на свежей ОС уже
    отсутствуют. На каждую снятую ВМ эмитим `vm.deleted` c `reason=server_clean`.

    Возвращает `{deleted, names}`.
    """
    vms = await repo.list_for_hub(db, hub.id)
    removed: list[dict] = []
    for vm in vms:
        removed.append(
            {"vm_id": vm.id, "name": vm.name, "department_id": vm.department_id}
        )
        await repo.delete(db, vm)
    if removed:
        await db.commit()
    for item in removed:
        audit_service.emit(
            "vm.deleted", target_id=item["vm_id"], target_type="vm",
            status="success", allowed=True,
            details={
                "reason": "server_clean",
                "name": item["name"],
                "department_id": item["department_id"],
                "hub_server_id": hub.id,
            },
        )
    return {"deleted": len(removed), "names": [i["name"] for i in removed]}


# ── статус-sweep ВМ (domstate/ping/ssh) ──────────────────────────────────────

# Источник частого статус-прогона ВМ — идёт в `details.source` audit-события,
# чтобы SIEM отличал sweep от ручного дисптача.
SOURCE_VM_STATUS_SWEEP = "auto_vm_status_sweep"

# Sentinel для кэша hub'ов внутри одного прогона (None — валидный «hub не найден»).
_HUB_UNCACHED = object()


async def _dispatch_vm_status(
    db: AsyncSession,
    *,
    vm: Vm,
    hub,
    actor_id: str | None,
    request_id: str | None,
) -> str | None:
    """Best-effort поставить `vm.status` для одной ВМ. Вернуть task_id либо None.

    Зеркало серверного `probe_server_power`/`_dispatch_one`: ConflictError
    (idempotent-replay) и ServiceUnavailableError (воркер/redis недоступны)
    уводятся в failure-audit и возвращают None, а не пробрасываются — один
    провал не должен валить остальные ВМ sweep'а. Payload несёт адресацию hub'а
    + имя ВМ и, если известен, LAN-адрес гостя (`guest_ip`) для ping/ssh.
    """
    payload = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "name": vm.name,
    }
    if vm.ip_address is not None:
        payload["guest_ip"] = str(vm.ip_address)
    try:
        task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
            db=db,
            task_kind=VmTaskKind.VM_STATUS,
            target_server_id=hub.id,
            target_resource_id=vm.id,
            payload=payload,
            created_by=actor_id,
            request_id=request_id,
            idempotency_key=None,
        )
        await db.commit()
    except (ConflictError, ServiceUnavailableError) as exc:
        await db.rollback()
        reason = (
            "idempotent_conflict"
            if isinstance(exc, ConflictError)
            else "worker_unreachable"
        )
        audit_service.emit(
            "vm.status", target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={
                "reason": reason,
                "task_kind": VmTaskKind.VM_STATUS.value,
                "source": SOURCE_VM_STATUS_SWEEP,
                "hub_server_id": hub.id,
                "department_id": vm.department_id,
            },
        )
        return None
    except Exception:  # noqa: BLE001 — статус-sweep не должен падать
        await db.rollback()
        logger.warning(
            "vm status-sweep dispatch failed vm_id=%s", vm.id, exc_info=True,
        )
        audit_service.emit(
            "vm.status", target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={
                "reason": "dispatch_error",
                "task_kind": VmTaskKind.VM_STATUS.value,
                "source": SOURCE_VM_STATUS_SWEEP,
                "hub_server_id": hub.id,
                "department_id": vm.department_id,
            },
        )
        return None
    audit_service.emit(
        "vm.status", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": VmTaskKind.VM_STATUS.value,
            "source": SOURCE_VM_STATUS_SWEEP,
            "hub_server_id": hub.id,
            "department_id": vm.department_id,
            "idempotent_hit": idempotent_hit,
        },
    )
    return task_id


async def fanout_vm_status_sweep(
    db: AsyncSession,
    *,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    """Частый прогон статус-пробы (`vm.status`) по ВСЕМ активным ВМ.

    Зеркало серверного `fanout_power_sweep`: населённость — все ВМ платформы
    (`repo.list_all_active`), а ставится только `vm.status` — питание домена
    (domstate) + ping/ssh гостя. Так питание и доступность гостей держатся
    актуальными между lifecycle-операциями. ВМ, чей hub отсутствует или списан,
    пропускаем (worker-операции туда не адресуемы).

    Cap `auto_inventory_fanout_max` — тот же throttle против шторма задач; при
    превышении режем хвост и эмитим `vm_status_sweep.truncated`. Hub'ы кэшируем
    в пределах прогона, чтобы не перезапрашивать один и тот же сервер под каждой
    его ВМ. Возвращает `{total_vms, processed, dispatched_tasks, truncated}`.
    """
    cap = get_settings().auto_inventory_fanout_max
    vms = await repo.list_all_active(db, limit=cap)
    total_vms = await repo.count_all_active(db)
    truncated = max(0, total_vms - cap)
    if truncated:
        audit_service.emit(
            "vm_status_sweep.truncated",
            target_id=None, target_type="vm",
            status="warning", allowed=True,
            details={
                "total_vms": total_vms,
                "cap": cap,
                "truncated_count": truncated,
            },
        )

    processed = 0
    dispatched_tasks = 0
    hub_cache: dict[str, object] = {}
    for vm in vms:
        hub = hub_cache.get(vm.hub_server_id, _HUB_UNCACHED)
        if hub is _HUB_UNCACHED:
            hub = await server_repo.get_by_id(db, vm.hub_server_id)
            hub_cache[vm.hub_server_id] = hub
        if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
            continue
        task_id = await _dispatch_vm_status(
            db, vm=vm, hub=hub,
            actor_id=actor_id, request_id=request_id,
        )
        processed += 1
        if task_id is not None:
            dispatched_tasks += 1

    return {
        "total_vms": total_vms,
        "processed": processed,
        "dispatched_tasks": dispatched_tasks,
        "truncated": truncated,
    }


def build_vm_probe_target(vm: Vm, hub) -> dict:
    """Плоская цель статус-пробы ВМ для воркер-loop'а (domstate + ping/ssh гостя).

    Несёт адресацию hub'а (проба идёт через управляющую SSH-сессию к нему) плюс
    имя домена, сетевой режим и, если известен, LAN-адрес гостя. Зеркалит поля,
    которые `_dispatch_vm_status` кладёт в task-payload, но без диспатча задачи.
    """
    return {
        "vm_id": vm.id,
        "vm_name": vm.name,
        "department_id": vm.department_id,
        "network_mode": vm.network_mode,
        "guest_ip": str(vm.ip_address) if vm.ip_address is not None else None,
        "hub_server_id": hub.id,
        "hub_host": str(hub.ip_address),
        "hub_ssh_port": hub.ssh_port,
        "hub_is_managed": hub.is_managed,
        "hub_management_user": hub.management_user,
    }


async def enumerate_vm_probe_targets(db: AsyncSession) -> tuple[list[dict], bool]:
    """Перечислить ВМ-цели пробинга (все активные), capped.

    Возвращает `(targets, truncated)`. ВМ, чей hub отсутствует или списан,
    пропускаем (проба к ним не адресуема). Hub'ы кэшируем в пределах прогона,
    чтобы не перезапрашивать один и тот же сервер под каждой его ВМ. Диспатча
    задач нет: воркер снимает сигналы сам из фонового loop'а.
    """
    cap = get_settings().auto_inventory_fanout_max
    vms = await repo.list_all_active(db, limit=cap)
    total = await repo.count_all_active(db)
    truncated = total > cap

    targets: list[dict] = []
    hub_cache: dict[str, object] = {}
    for vm in vms:
        hub = hub_cache.get(vm.hub_server_id, _HUB_UNCACHED)
        if hub is _HUB_UNCACHED:
            hub = await server_repo.get_by_id(db, vm.hub_server_id)
            hub_cache[vm.hub_server_id] = hub
        if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
            continue
        targets.append(build_vm_probe_target(vm, hub))
    return targets, truncated


# ── reconcile упавших vm.create ──────────────────────────────────────────────

# Значение worker'ского `TaskStatus.FAILED` в строке `tasks.status`. Только
# на нём reconcile удаляет ВМ — `queued`/`running`/отсутствие задачи считаются
# «ещё в полёте / неоднозначно» и ВМ не трогаются (удаление разрушительно).
_TASK_STATUS_FAILED = "failed"


async def _dispatch_vm_delete_cleanup(
    db: AsyncSession,
    *,
    hub,
    vm: Vm,
    actor_id: str | None,
    request_id: str | None,
) -> str | None:
    """Best-effort dispatch `vm.delete` для очистки домена на хабе (reconcile).

    В отличие от пользовательского `delete_vm`, тут нет request'а и
    idempotency-header'а — задача ставится системно (`created_by=actor_id`
    воркер-бота). Провал dispatch'а (worker недоступен / idempotent-конфликт)
    не должен мешать удалению строк ВМ, поэтому возвращаем None, а не бросаем.
    """
    payload = {**_hub_payload(hub), "vm_id": vm.id, "vm_name": vm.name}
    try:
        return await worker_client.dispatch_task(
            db=db,
            task_kind=VmTaskKind.VM_DELETE,
            target_server_id=hub.id,
            target_resource_id=vm.id,
            payload=payload,
            created_by=actor_id,
            request_id=request_id,
            idempotency_key=None,
        )
    except (ConflictError, ServiceUnavailableError):
        return None


async def reconcile_failed_vm_creates(
    db: AsyncSession,
    *,
    actor_id: str | None,
    request_id: str | None = None,
) -> dict:
    """Найти ВМ с провалившимся `vm.create` и удалить их (+ уведомить создателя).

    Периодический прогон (worker-scheduler → internal-эндпоинт). Для каждой ВМ
    в `busy_state='creating'` смотрим статус её самой свежей `vm.create`-задачи
    в worker-БД. Удаляем ВМ ТОЛЬКО если задача в терминально-провальном статусе
    (`failed`): `queued`/`running`/`succeeded`/отсутствие задачи не трогаем —
    удаление разрушительно, при любой неоднозначности пропускаем.

    Удаление: best-effort `vm.delete` на хаб (undefine домена, если хаб жив) +
    каскадное удаление строк ВМ (`repo.delete` → FK ON DELETE CASCADE для
    `vm_disks` / `vm_snapshots` / `vm_package_inventory` / `server_account_vms`).
    На каждую удалённую ВМ эмитим `vm.create_failed` (WARNING) с actor'ом =
    исходным создателем (`created_by`) и причиной из `task.last_error`.

    Идемпотентно: повторный тик по уже удалённой ВМ её не видит (строки нет);
    ВМ без failed-задачи остаются нетронутыми. Ошибка на одной ВМ не мешает
    остальным (per-VM try/except).

    Возвращает `{ok, checked, deleted, skipped}`.
    """
    creating = await repo.list_by_busy_state(db, VmBusyState.CREATING.value)
    checked = len(creating)
    deleted = 0
    skipped = 0
    for vm in creating:
        try:
            latest = await worker_client.get_latest_task_status(
                target_resource_id=vm.id,
                task_kind=VmTaskKind.VM_CREATE.value,
            )
            if latest is None or latest.get("status") != _TASK_STATUS_FAILED:
                # Задачи нет либо она ещё жива / успешна — не трогаем.
                skipped += 1
                continue

            create_task_id = latest.get("task_id")
            failure_reason = latest.get("last_error")
            created_by = vm.created_by
            name = vm.name
            dept = vm.department_id

            hub = await server_repo.get_by_id(db, vm.hub_server_id)
            cleanup_task_id: str | None = None
            if hub is not None and hub.status != ServerStatus.DECOMMISSIONED:
                cleanup_task_id = await _dispatch_vm_delete_cleanup(
                    db, hub=hub, vm=vm,
                    actor_id=actor_id, request_id=request_id,
                )

            await repo.delete(db, vm)
            await db.commit()

            audit_service.emit(
                "vm.create_failed",
                actor_id=created_by,
                actor_type="user",
                target_id=vm.id, target_type="vm",
                status="failure", allowed=True,
                department_id=dept,
                details={
                    "reason": "create_task_failed",
                    "name": name,
                    "department_id": dept,
                    "create_task_id": create_task_id,
                    "cleanup_task_id": cleanup_task_id,
                    "last_error": failure_reason,
                },
            )
            deleted += 1
        except Exception:  # noqa: BLE001 — одна ВМ не должна ронять весь прогон
            await db.rollback()
            logger.exception(
                "vm create-reconcile failed for vm_id=%s", vm.id,
            )
            skipped += 1
    return {"ok": True, "checked": checked, "deleted": deleted, "skipped": skipped}


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
    vm_reservation.ensure_not_service_locked(vm, action="vm.reserved")
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
    # `testing_done` сервисной брони перебивается человеческой бронью.
    vm_reservation.clear_service_reservation(vm)
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
    vm_reservation.ensure_not_service_locked(vm, action="vm.released")
    # `testing_done` сервисной брони снимает любой с правом release —
    # как `acknowledge-testing-done` у серверов.
    acknowledge = vm.service_busy_state is not None
    if (
        not acknowledge
        and vm.status != VM_STATUS_FREE and vm.status != identity.username and not _is_vm_admin(identity, vm)
    ):
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
    vm_reservation.clear_service_reservation(vm)
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
    vm_reservation.ensure_not_service_locked(vm, action="vm.status_updated")
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
    vm_reservation.clear_service_reservation(vm)
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
    reservation.ensure_not_acs_locked(identity, hub)
    if not hub.is_managed:
        audit_service.emit(
            "vms_hub.prepared", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "prepare_required"},
        )
        raise ConflictError(
            error_code="PREPARE_REQUIRED",
            message="Server must be prepared for management before it can become a VMS-hub",
        )
    # virtualization=None — ещё не пробовали (флаг ставит только callback этого
    # же prepare); реальный precheck /dev/kvm делает worker-задача (VMS_HUB_NO_KVM).
    # Блокируем только явный False — прошлый prepare уже установил, что KVM нет.
    if hub.virtualization is False:
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


# ── vm.prepare / mgmt-креды / сеть ───────────────────────────────────────────


async def _stash_vm_mgmt_creds(
    vm: Vm,
    creds: dict[str, str],
    audit_action: str,
) -> str:
    """Положить управляющий материал ВМ в Redis-stash, вернуть ключ для payload.

    Симметрично серверному provision/rotate: plaintext ключа+пароля в
    task-payload не едет, туда кладётся только `creds_stash_key`. Недоступный
    Redis → 503 (`WORKER_REDIS_NOT_CONFIGURED`); runtime-фейл записи → чистим
    осиротевший ключ best-effort и отдаём 503.
    """
    from src.core.exceptions import ServiceUnavailableError

    stash_key = worker_client.dispatch_creds_key(dispatch_creds_id())
    try:
        await worker_client.store_dispatch_creds(stash_key, creds)
    except ServiceUnavailableError:
        audit_service.emit(
            audit_action, target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "creds_store_unavailable", "department_id": vm.department_id},
        )
        raise
    except Exception as exc:  # noqa: BLE001
        await worker_client.delete_dispatch_creds(stash_key)
        audit_service.emit(
            audit_action, target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={
                "reason": "creds_store_failed",
                "department_id": vm.department_id,
                "error_class": type(exc).__name__,
            },
        )
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_UNAVAILABLE",
            message="Failed to stash VM management credentials before dispatch",
        ) from exc
    return stash_key


def _store_new_mgmt_creds(vm: Vm) -> dict[str, str]:
    """Сгенерировать per-VM управляющие креды, зашифровать и положить в модель.

    Public-ключ — открытым текстом; private и пароль — envelope AES-256-GCM со
    своим AAD (привязка к vm_id). Ставит `mgmt_creds_pending_apply=True`.
    Возвращает plaintext-набор для отдачи воркеру в payload (установка в госте).
    """
    private_pem, public_openssh, password = generate_management_material()
    vm.mgmt_ssh_public_key = public_openssh
    vm.mgmt_ssh_private_key_encrypted = secrets_service.encrypt(
        private_pem, aad=secrets_service.aad_for_vm_mgmt_ssh_key(vm.id),
    )
    vm.mgmt_password_encrypted = secrets_service.encrypt(
        password, aad=secrets_service.aad_for_vm_mgmt_password(vm.id),
    )
    vm.mgmt_creds_pending_apply = True
    return {
        "public_key": public_openssh,
        "private_key": private_pem,
        "password": password,
    }


def _decrypt_vm_mgmt_creds(vm: Vm) -> dict[str, str] | None:
    """Расшифровать сохранённый управляющий материал managed-ВМ для stash'а.

    Managed-ВМ хранит per-VM управляющую пару + пароль в модели (`mgmt_*`,
    envelope AES-256-GCM с AAD по vm_id). Пост-создательные guest-задачи (учётки,
    диски, guest-часть astra/allta-update) ходят в гостя ключом управляющего
    пользователя — базовая учётка `u` на managed-ВМ снесена. Возвращает
    `{management_user, public_key, private_key, password}` либо None, если ВМ не
    managed или ключ ещё не установлен (воркер сработает по `u`/`1`).
    """
    if not vm.is_managed or vm.mgmt_ssh_private_key_encrypted is None:
        return None
    private_key = secrets_service.decrypt(
        vm.mgmt_ssh_private_key_encrypted,
        aad=secrets_service.aad_for_vm_mgmt_ssh_key(vm.id),
    )
    password = ""
    if vm.mgmt_password_encrypted is not None:
        password = secrets_service.decrypt(
            vm.mgmt_password_encrypted,
            aad=secrets_service.aad_for_vm_mgmt_password(vm.id),
        )
    return {
        "management_user": vm.mgmt_user or "",
        "public_key": vm.mgmt_ssh_public_key or "",
        "private_key": private_key,
        "password": password,
    }


async def stash_existing_vm_mgmt_creds(vm: Vm, audit_action: str) -> str | None:
    """Расшифровать и застэшить управляющий материал managed-ВМ; вернуть stash-ключ.

    Для не-managed / ещё не онбординнутой ВМ возвращает None — guest-задача уедет
    без `creds_stash_key` и воркер сработает по дефолтным кредам образа `u`/`1`.
    Недоступный Redis → 503 (см. `_stash_vm_mgmt_creds`).
    """
    creds = _decrypt_vm_mgmt_creds(vm)
    if creds is None:
        return None
    return await _stash_vm_mgmt_creds(vm, creds, audit_action=audit_action)


async def prepare_vm(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
) -> tuple[Vm, str]:
    """Dispatch VM_PREPARE — онбординг управления ВМ. Право `(vm, vm_prepare)`.

    server_service генерит per-VM управляющую SSH-пару + пароль, шифрует и
    кладёт в `mgmt_*` (`mgmt_creds_pending_apply=True`), а plaintext-материал
    складывает в Redis-stash (`creds_stash_key`); в payload едет ссылка на
    stash + дефолт-креды образа (`u:1`). Воркер заходит под образными кредами,
    читает управляющий материал из stash'а, ставит новый ключ+пароль, сносит
    базовую учётку и подтверждает callback'ом `POST /internal/vms/{id}/prepared`
    (→ `is_managed=True`).
    """
    vm, hub = await _load_vm_for_managed_op(
        db, identity, vm_id,
        action_perm=Action.VM_PREPARE,
        audit_action="vm.prepared", op="prepare",
    )
    if vm.mgmt_creds_pending_apply:
        audit_service.emit(
            "vm.prepared", target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "rotation_pending", "department_id": vm.department_id},
        )
        raise ConflictError(
            error_code="VM_MGMT_ROTATION_PENDING",
            message=(
                "Management credentials onboarding/rotation is already in "
                "progress (pending apply). Wait for the worker callback."
            ),
        )
    settings = get_settings()
    new_creds = _store_new_mgmt_creds(vm)
    mgmt_user = (await management_user_config.get_config(db)).login
    stash_key = await _stash_vm_mgmt_creds(
        vm,
        {
            "management_user": mgmt_user,
            "public_key": new_creds["public_key"],
            "private_key": new_creds["private_key"],
            "password": new_creds["password"],
        },
        audit_action="vm.prepared",
    )
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "guest_ip": str(vm.ip_address) if vm.ip_address is not None else None,
        "operation": "prepare",
        "image_user": settings.vm_image_default_user,
        "image_password": settings.vm_image_default_password,
        "creds_stash_key": stash_key,
    }
    vm.busy_state = VmBusyState.PREPARING.value
    vm.busy_since = datetime.now(timezone.utc)
    try:
        task_id = await _dispatch_vm_task(
            db=db, identity=identity, request=request,
            task_kind=VmTaskKind.VM_PREPARE, hub=hub, vm=vm,
            payload=payload_task, audit_action="vm.prepared",
        )
    except Exception:
        # Stash осиротел — задача в брокер не доехала, воркер за материалом
        # не пойдёт; чистим ключ, чтобы plaintext не висел до TTL.
        await worker_client.delete_dispatch_creds(stash_key)
        raise
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.prepared", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "operation": "prepare", "department_id": vm.department_id},
    )
    return vm, task_id


async def rotate_mgmt_creds(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
) -> tuple[Vm, str]:
    """Dispatch VM_PREPARE (ротация) — перевыпустить per-VM управляющие креды.

    Право `(vm, vm_prepare)`. 409, если предыдущий онбординг/ротация ещё не
    подтверждены (`mgmt_creds_pending_apply`). Генерит новый материал, шифрует в
    `mgmt_*`, plaintext складывает в Redis-stash (в payload — только
    `creds_stash_key`). Previous-зеркал у ВМ нет — при сбое ВМ перекатывается
    заново.
    """
    vm, hub = await _load_vm_for_managed_op(
        db, identity, vm_id,
        action_perm=Action.VM_PREPARE,
        audit_action="vm.creds_rotated", op="creds_rotate",
    )
    if not vm.is_managed:
        audit_service.emit(
            "vm.creds_rotated", target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "prepare_required", "department_id": vm.department_id},
        )
        raise ConflictError(
            error_code="VM_PREPARE_REQUIRED",
            message="VM is not prepared for management; run POST /vms/{id}/prepare first",
        )
    if vm.mgmt_creds_pending_apply:
        audit_service.emit(
            "vm.creds_rotated", target_id=vm.id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "rotation_pending", "department_id": vm.department_id},
        )
        raise ConflictError(
            error_code="VM_MGMT_ROTATION_PENDING",
            message=(
                "Management credentials rotation is already in progress "
                "(pending apply). Wait for the worker callback."
            ),
        )
    new_creds = _store_new_mgmt_creds(vm)
    stash_key = await _stash_vm_mgmt_creds(
        vm,
        {
            "management_user": vm.mgmt_user,
            "public_key": new_creds["public_key"],
            "private_key": new_creds["private_key"],
            "password": new_creds["password"],
        },
        audit_action="vm.creds_rotated",
    )
    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "guest_ip": str(vm.ip_address) if vm.ip_address is not None else None,
        "operation": "rotate_creds",
        "creds_stash_key": stash_key,
    }
    vm.busy_state = VmBusyState.PREPARING.value
    vm.busy_since = datetime.now(timezone.utc)
    try:
        task_id = await _dispatch_vm_task(
            db=db, identity=identity, request=request,
            task_kind=VmTaskKind.VM_PREPARE, hub=hub, vm=vm,
            payload=payload_task, audit_action="vm.creds_rotated",
        )
    except Exception:
        await worker_client.delete_dispatch_creds(stash_key)
        raise
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.creds_rotated", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "department_id": vm.department_id},
    )
    return vm, task_id


async def set_network(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    payload: VmNetworkRequest,
) -> tuple[Vm, str]:
    """Dispatch VM_SET_NETWORK — сменить сетевой режим ВМ. Право `(vm, vm_net_manage)`.

    bridge: адрес берётся из `ip_address` (проверяется на занятость) либо
    аллоцируется из `pool_id`; провижн статики в госте + правка XML. nat: адрес
    выдаёт libvirt (domifaddr) — `ip_address` зануляем. Гейт брони + lifecycle-
    lock. Ставит busy_state=networking.
    """
    with emit_denied_on_authz_error(
        "vm.net_updated", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.VM, Action.VM_NET_MANAGE)
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.net_updated", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_not_busy(vm, action="vm.set_network")
    _ensure_bookable(identity, vm, action="vm.set_network")
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )

    mode = payload.network_mode.value
    resolved_ip: str | None = None
    pool = None
    if payload.network_mode == VmNetworkMode.BRIDGE:
        resolved_ip, pool = await ip_pool_svc.resolve_bridge_ip(
            db, identity,
            department_id=vm.department_id,
            ip_address=str(payload.ip_address) if payload.ip_address is not None else None,
            pool_id=payload.pool_id,
            exclude_vm_id=vm.id,
            audit_action="vm.net_updated",
            audit_target_id=vm.id,
        )
        if resolved_ip is None:
            # bridge без нового адреса — оставляем текущий (worker переводит XML).
            resolved_ip = str(vm.ip_address) if vm.ip_address is not None else None

    payload_task = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "network_mode": mode,
        "ip_address": resolved_ip,
        "gateway": str(pool.gateway) if pool is not None and pool.gateway is not None else None,
        "netmask": pool.netmask if pool is not None else None,
        "dns": list(pool.dns) if pool is not None and pool.dns is not None else None,
    }
    vm.network_mode = mode
    if payload.network_mode == VmNetworkMode.NAT:
        vm.ip_address = None
    elif resolved_ip is not None:
        vm.ip_address = resolved_ip
    vm.busy_state = VmBusyState.NETWORKING.value
    vm.busy_since = datetime.now(timezone.utc)
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_SET_NETWORK, hub=hub, vm=vm,
        payload=payload_task, audit_action="vm.net_updated",
    )
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.net_updated", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "network_mode": mode, "ip_address": resolved_ip, "department_id": vm.department_id},
    )
    return vm, task_id


# ── autostart ────────────────────────────────────────────────────────────────


async def set_autostart(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    enabled: bool,
) -> tuple[Vm, str]:
    """Dispatch VM_SET_AUTOSTART (`virsh autostart [--disable]`). Право `(vm, vm_power)`.

    Автозапуск — свойство из семейства питания, поэтому гейтится
    тем же правом `vm_power`, что и старт/стоп. Флаг `autostart` выставляется
    оптимистично; воркер применяет его в libvirt.
    """
    with emit_denied_on_authz_error(
        "vm.autostart_set", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id, "enabled": enabled}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VM_POWER
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.autostart_set", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_not_busy(vm, action="vm.set_autostart")
    _ensure_bookable(identity, vm, action="vm.set_autostart")
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
        "autostart": enabled,
    }
    vm.autostart = enabled
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_SET_AUTOSTART, hub=hub, vm=vm,
        payload=payload, audit_action="vm.autostart_set",
    )
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.autostart_set", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "enabled": enabled, "department_id": vm.department_id},
    )
    return vm, task_id


# ── create-default-vms (развернуть пресеты отдела на hub) ─────────────────────


def _preset_deploy_conflict_reason(preset) -> str:
    """reason-строка пропуска пресета в аудите (для UI/SIEM)."""
    return (
        "already_deployed_on_hub"
        if preset.network_mode == VmNetworkMode.NAT
        else "already_deployed_global"
    )


async def _preset_already_deployed(db: AsyncSession, preset, hub) -> bool:
    """Проверка deploy-once: bridge — 1 раз глобально (по имени в отделе),
    nat — 1 раз на hub-сервер (по имени на hub'е)."""
    if preset.network_mode == VmNetworkMode.NAT:
        return await repo.exists_on_hub_by_name(db, hub.id, preset.name)
    return await repo.exists_in_department_by_name(db, preset.department_id, preset.name)


def _capacity_check_total(hub, existing: dict[str, int], planned: dict[str, int]) -> None:
    """Ёмкость для батча пресетов: Σ(уже созданных) + Σ(планируемых) ≤ hub."""
    hub_cpu = hub.cpu_threads or hub.cpu_cores
    hub_ram = hub.ram_total_mb
    hub_disk_gb = sum(d.size_gb or 0 for d in getattr(hub, "_hub_disks", []))
    checks = [
        ("cpu", hub_cpu, existing["cpu"] + planned["cpu"]),
        ("ram_mb", hub_ram, existing["ram_mb"] + planned["ram_mb"]),
        ("disk_gb", hub_disk_gb, existing["disk_gb"] + planned["disk_gb"]),
    ]
    for dim, capacity, requested in checks:
        if capacity and requested > capacity:
            raise ConflictError(
                error_code="VM_CAPACITY_EXCEEDED",
                message=(
                    f"Hub {dim} capacity exceeded deploying presets: requested "
                    f"total {requested} > hub {capacity}"
                ),
                details={"dimension": dim, "hub_capacity": capacity, "requested_total": requested},
            )


async def _next_stand_number(db: AsyncSession, department_id: str) -> int:
    """Следующий свободный номер стенда отдела (общий пул servers+vm).

    Используется для VM, разворачиваемых из пресета без явного `number` —
    карточка ВМ не может остаться без номера (`vms.number` NOT NULL).
    """
    server_max = await server_repo.max_number_for_department(db, department_id)
    vm_max = await repo.max_number_for_department(db, department_id)
    return max(server_max, vm_max) + 1


async def create_default_vms(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    server_id: str,
) -> tuple[str, list[dict], list[dict]]:
    """Развернуть пресеты отдела на hub'е (dispatch серии VM_CREATE).

    Право `(vm, create)`. Deploy-once: bridge-пресет — 1 раз глобально,
    nat-пресет — 1 раз на hub-сервер; уже развёрнутые пропускаются. Полный
    повтор (разворачивать нечего) → 409 VM_PRESETS_ALREADY_DEPLOYED. Ёмкость
    hub'а проверяется по сумме разворачиваемых пресетов. Возвращает
    (server_id, created[], skipped[]).
    """
    with emit_denied_on_authz_error(
        "vm_preset.deployed", target_id=server_id, target_type="server",
        extra_details={"server_id": server_id}, identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.VM, Action.CREATE)
    hub = await server_repo.get_by_id(db, server_id)
    if hub is None or hub.department_id != identity.department_id:
        audit_service.emit(
            "vm_preset.deployed", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "hub_not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="HUB_NOT_FOUND", message="Hub server not found")
    if not hub.is_vms_hub:
        audit_service.emit(
            "vm_preset.deployed", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "hub_not_prepared"},
        )
        raise ConflictError(
            error_code="HUB_NOT_PREPARED",
            message="Server is not prepared as a VMS-hub; run prepare-vms-hub first",
        )
    presets = await vm_preset_repo.list_all_in_department(db, identity.department_id)
    if not presets:
        raise NotFoundError(
            error_code="VM_NO_PRESETS",
            message="No VM presets defined for the department; create presets first",
        )

    # Отфильтровать уже развёрнутые (deploy-once), сумму — на ёмкость.
    deployable = []
    skipped: list[dict] = []
    for preset in presets:
        if await _preset_already_deployed(db, preset, hub):
            skipped.append({
                "preset_id": preset.id, "name": preset.name,
                "reason": _preset_deploy_conflict_reason(preset),
            })
            continue
        deployable.append(preset)

    if not deployable:
        audit_service.emit(
            "vm_preset.deployed", target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "all_deployed", "skipped": len(skipped), "department_id": hub.department_id},
        )
        raise ConflictError(
            error_code="VM_PRESETS_ALREADY_DEPLOYED",
            message="All department presets are already deployed (bridge globally / nat on this hub)",
            details={"skipped": len(skipped)},
        )

    hub._hub_disks = await disk_repo.list_all_for_server(db, hub.id)
    existing = await repo.sum_resources_for_hub(db, hub.id)
    planned = {
        "cpu": sum(p.cpu for p in deployable),
        "ram_mb": sum(p.ram_mb for p in deployable),
        "disk_gb": sum(p.disk_gb for p in deployable),
    }
    _capacity_check_total(hub, existing, planned)

    created: list[dict] = []
    for preset in deployable:
        box_url, box_os_versions = await _resolve_box_url(db, preset.box, hub.id)
        number = preset.number
        if number is None:
            number = await _next_stand_number(db, preset.department_id)
        vm_data = {
            "id": new_vm_id(),
            "name": preset.name,
            "number": number,
            "hub_server_id": hub.id,
            "department_id": preset.department_id,
            "os_version": preset.os_version,
            "box": preset.box,
            "network_mode": preset.network_mode,
            "ip_address": str(preset.fixed_ip) if preset.fixed_ip is not None else None,
            "status": VM_STATUS_FREE,
            "power_state": VmPowerState.UNKNOWN.value,
            "cpu": preset.cpu,
            "ram_mb": preset.ram_mb,
            "disk_gb": preset.disk_gb,
            "autostart": False,
            "cred_strategy": "per_snapshot",
            "busy_state": VmBusyState.CREATING.value,
            "busy_since": datetime.now(timezone.utc),
            "created_by": identity.user_id,
        }
        try:
            vm = await repo.create(db, vm_data)
        except IntegrityError as exc:
            await db.rollback()
            audit_service.emit(
                "vm_preset.deployed", target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "duplicate", "preset": preset.name, "department_id": hub.department_id},
            )
            raise ConflictError(
                error_code="VM_DUPLICATE",
                message=(
                    f"Preset '{preset.name}' collides with an existing VM name or "
                    "number on the hub"
                ),
                details={"preset": preset.name},
            ) from exc
        payload_task = {
            **_hub_payload(hub),
            "vm_id": vm.id,
            "name": vm.name,
            "box": vm.box,
            "box_url": box_url,
            "os_version": vm.os_version,
            "os_versions": box_os_versions,
            "network_mode": vm.network_mode,
            "ip_address": vm_data["ip_address"],
            "cpu": vm.cpu,
            "ram_mb": vm.ram_mb,
            "disk_gb": vm.disk_gb,
            "autostart": vm.autostart,
            "cred_strategy": vm.cred_strategy,
            "from_preset_id": preset.id,
        }
        task_id = await _dispatch_vm_task(
            db=db, identity=identity, request=request,
            task_kind=VmTaskKind.VM_CREATE, hub=hub, vm=vm,
            payload=payload_task, audit_action="vm_preset.deployed",
        )
        created.append({
            "preset_id": preset.id, "vm_id": vm.id, "name": vm.name, "task_id": task_id,
        })
        audit_service.emit(
            "vm.created", target_id=vm.id, target_type="vm",
            status="success", allowed=True,
            details={"task_id": task_id, "hub_server_id": hub.id, "name": vm.name,
                     "from_preset_id": preset.id, "department_id": vm.department_id},
        )

    await db.commit()
    audit_service.emit(
        "vm_preset.deployed", target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={"created": len(created), "skipped": len(skipped), "department_id": hub.department_id},
    )
    return server_id, created, skipped


# ── консоль (ssh / vnc / serial) ─────────────────────────────────────────────


async def console_access(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    kind: str,
) -> dict:
    """Выдать доступ к консоли ВМ. Право `(vm, view)` + бронь.

    Возвращает контракт подключения UI к websockify/PTY-прокси: короткоживущий
    токен + hub-хост + порт/serial-путь/пользователь по типу консоли. Реальный
    проброс держит прокси (ставится отдельной волной), server_service токен не
    хранит и plaintext-креды в ответ не кладёт — их прокси тянет через internal
    mgmt-credentials.
    """
    if kind not in VM_CONSOLE_KINDS:
        raise DomainValidationError(
            error_code="INVALID_VM_CONSOLE_KIND",
            message=f"kind must be one of {sorted(VM_CONSOLE_KINDS)}",
        )
    await permissions.require_resource_action(
        db, identity, EntityType.VM, vm_id, Action.VIEW
    )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_bookable(identity, vm, action="vm.console")
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    settings = get_settings()
    guest_ip = str(vm.ip_address) if vm.ip_address is not None else None
    hub_ip = str(hub.ip_address)
    ttl = settings.vm_console_token_ttl_seconds
    ws_path = f"/vm-console/{kind}/{vm.id}"
    result: dict = {
        "vm_id": vm.id,
        "kind": kind,
        "token": new_console_token(),
        "expires_in": ttl,
        "ws_path": ws_path,
        "ws_url": None,
        "port": None,
        "serial_path": None,
        "username": None,
        "password": None,
    }
    if kind == "ssh":
        # SSH идёт к гостю; пароль/ключ прокси берёт по internal mgmt-credentials.
        result["host"] = guest_ip
        result["port"] = 22
        result["username"] = vm.mgmt_user or settings.vm_image_default_user
    elif kind in VM_GRAPHICS_CONSOLE_KINDS:
        # vnc/spice: графика через websockify-прокси на hub'е. Токен подписан —
        # прокси проверяет его по общему секрету, round-trip в server_service не
        # нужен. Порт дисплея, если воркер его сообщил (graphics_port), кладём в
        # токен; иначе прокси резолвит через virsh.
        result["host"] = hub_ip
        result["port"] = vm.graphics_port
        result["ws_url"] = f"{settings.vm_console_proxy_ws_base}{ws_path}"
        result["token"] = console_token.issue(
            secret=settings.vm_console_token_secret,
            vm_id=vm.id,
            kind=kind,
            hub_ip=hub_ip,
            hub_server_id=vm.hub_server_id,
            department_id=vm.department_id,
            ssh_port=hub.ssh_port,
            port=vm.graphics_port,
            domain=vm.name,
            ttl_seconds=ttl,
        )
    else:  # serial
        result["host"] = hub_ip
    audit_service.emit(
        "vm.console_accessed", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"kind": kind, "department_id": vm.department_id},
    )
    return result


async def resolve_vm_console_credentials(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    account_id: str,
) -> tuple[Vm, object, dict[str, str | None]]:
    """Загрузить ВМ+hub и расшифровать креды учётки для интерактивной консоли ВМ.

    Зеркало серверного `server_account.resolve_console_credentials`, но привязка
    идёт к ВМ (`server_account_vms`), а не к серверу. Проверки по порядку:

    * видимость ВМ (свой отдел / инстанс-грант, иначе 404 VM_NOT_FOUND);
    * бронь (`_ensure_bookable` → 409 VM_RESERVED, если ВМ занята другим);
    * доступность hub'а (missing/decommissioned → 409 HUB_UNAVAILABLE);
    * видимость учётки (dept-isolation / грант, иначе 404 ACCOUNT_NOT_FOUND);
    * привязка учётки к этой ВМ (иначе 404 ACCOUNT_NOT_LINKED);
    * ролевой `console`/`view_password` на учётке (иначе 403 PERMISSION_DENIED) —
      у ВМ нет собственного `(vm, console)`-гранта, гейт целиком на учётке;
    * наличие сохранённого пароля (иначе 409 ACCOUNT_HAS_NO_PASSWORD) — вход в
      гостя идёт по паролю учётки.

    Возвращает `(vm, hub, {"login","password","ssh_private_key"})`; сами
    значения кладёт в Redis-stash вызывающий WS-endpoint, в start-сообщение
    воркеру едет только ссылка.
    """
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    _ensure_bookable(identity, vm, action="vm.console")
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )

    account = await account_repo.get_by_id(db, account_id)
    if account is None:
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
        )
    # Видимость учётки: свой отдел ЛИБО инстанс-грант на неё (тот же 404, что и
    # для несуществующей — не палим enumeration'ом факт чужой учётки).
    if identity.department_id != account.department_id and not await permissions.has_resource_grant(
        db, identity, EntityType.SERVER_ACCOUNT, account.id,
    ):
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND", message="Server account not found",
        )
    if not await account_repo.is_vm_linked(db, account.id, vm.id):
        raise NotFoundError(
            error_code="ACCOUNT_NOT_LINKED",
            message="Server account is not linked to this VM",
        )
    allowed = await permissions.has_account_action(
        db, identity, account, Action.CONSOLE,
    ) or await permissions.has_account_action(
        db, identity, account, Action.VIEW_PASSWORD,
    )
    if not allowed:
        raise AuthorizationError(
            error_code="PERMISSION_DENIED",
            message="No console access to this server account",
            details={"entity_type": "server_account", "action": "console"},
        )
    if account.password_encrypted is None:
        raise ConflictError(
            error_code="ACCOUNT_HAS_NO_PASSWORD",
            message=(
                "Selected account has no stored password; rotate it first or "
                "attach one before opening the VM console"
            ),
        )
    password = secrets_service.decrypt(
        account.password_encrypted,
        aad=secrets_service.aad_for_server_account_password(account.id),
    )
    ssh_private_key: str | None = None
    if account.ssh_private_key_encrypted is not None:
        ssh_private_key = secrets_service.decrypt(
            account.ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_account_ssh_key(account.id),
        )
    audit_service.emit(
        "server_account.bootstrap_resolved",
        target_id=account.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": account.login,
            "vm_id": vm.id,
            "department_id": account.department_id,
            "has_ssh_private_key": ssh_private_key is not None,
            "via": "vm_console",
        },
    )
    return vm, hub, {
        "login": account.login,
        "password": password,
        "ssh_private_key": ssh_private_key,
    }


# ── учётки / пакеты ВМ ───────────────────────────────────────────────────────


async def list_vm_accounts(
    db: AsyncSession, identity: IdentityContext, vm_id: str,
) -> list[dict]:
    """Учётки, привязанные к ВМ. Право `(vm, view)` + видимость (cross-dept → 404).

    Возвращает атрибуты учётки + `present_on_vm` (дрейф — привязка есть, в госте
    нет). Секретов не отдаёт (пароль/приватный ключ не читаются).
    """
    await permissions.require_resource_action(
        db, identity, EntityType.VM, vm_id, Action.VIEW
    )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    links = await account_repo.list_vm_links(db, vm_id)
    return [
        {
            "account_id": account.id,
            "login": account.login,
            "has_sudo": account.has_sudo,
            "unix_groups": list(account.unix_groups or []),
            "ssh_public_key": account.ssh_public_key,
            "present_on_vm": link.present_on_vm,
        }
        for link, account in links
    ]


async def list_packages(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    *,
    refresh: bool,
    patterns: list[str] | None = None,
) -> dict:
    """Инвентарь пакетов гостя ВМ. Право `(vm, view)` + видимость (cross-dept → 404).

    По умолчанию отдаёт сохранённый список (что записал воркер callback'ом
    `record_vm_packages`). `refresh=True` дополнительно диспатчит свежий probe
    `vm.list_packages` — ВМ обязана быть prepared (`is_managed`), иметь IP гостя
    и живой hub; результат придёт callback'ом. Read-only, брони не требует.

    `patterns` (shell glob) сужают probe — воркер OR-матчит по списку масок,
    зеркало серверного `installed_packages.list`. None → `["*"]` (все пакеты).
    Валидацию масок делает endpoint.
    """
    await permissions.require_resource_action(
        db, identity, EntityType.VM, vm_id, Action.VIEW
    )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)

    inventory = await vm_package_repo.get_for_vm(db, vm_id)
    result: dict = {
        "vm_id": vm.id,
        "packages": list(inventory.packages) if inventory is not None else [],
        "package_count": inventory.package_count if inventory is not None else 0,
        "source": inventory.source if inventory is not None else None,
        "synced_at": inventory.synced_at if inventory is not None else None,
        "dispatched": False,
        "task_id": None,
    }
    if not refresh:
        return result

    # Свежий probe: гость опрашивается по SSH через hub под управляющими кредами,
    # поэтому ВМ обязана быть prepared и иметь IP.
    if not vm.is_managed:
        raise ConflictError(
            error_code="VM_PREPARE_REQUIRED",
            message="VM is not prepared; run prepare before probing guest packages",
        )
    if vm.ip_address is None:
        raise ConflictError(
            error_code="VM_GUEST_IP_UNKNOWN",
            message="VM guest IP is unknown; cannot reach the guest to list packages",
        )
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    effective_patterns = patterns if patterns else ["*"]
    payload = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "guest_ip": str(vm.ip_address),
        # `patterns` — список масок (worker OR-матчит), `pattern` дублируем
        # (raw, первая маска) для back-compat и success-audit'а, как в
        # серверном installed_packages.list.
        "patterns": effective_patterns,
        "pattern": effective_patterns[0],
    }
    # Probe идёт на гостя по SSH через hub под управляющими кредами — стэшим
    # mgmt-материал ровно как для guest-задач (в payload едет только stash-ключ).
    stash_key = await stash_existing_vm_mgmt_creds(vm, "vm.packages_listed")
    if stash_key:
        payload["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_LIST_PACKAGES, hub=hub, vm=vm,
        payload=payload, audit_action="vm.packages_listed", stash_key=stash_key,
    )
    await db.commit()
    audit_service.emit(
        "vm.packages_listed", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "department_id": vm.department_id,
            "patterns": effective_patterns,
        },
    )
    result["dispatched"] = True
    result["task_id"] = task_id
    return result


# task_kind на действие мутации пакетов гостя ВМ. Зеркало серверного
# `_PACKAGES_ACTION_MAP`, но per-VM (не bulk): worker-таски `vm.<action>_packages`
# и per-action audit-action (past-tense, как у остальных VM-операций).
_VM_PACKAGES_ACTION_MAP: dict[str, tuple[str, str]] = {
    "install": (VmTaskKind.VM_INSTALL_PACKAGES, "vm.packages_installed"),
    "remove": (VmTaskKind.VM_REMOVE_PACKAGES, "vm.packages_removed"),
    "update": (VmTaskKind.VM_UPDATE_PACKAGES, "vm.packages_updated"),
}


async def mutate_packages(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    *,
    action: str,
    packages: list[str],
) -> tuple[Vm, str]:
    """Диспатч мутации пакетов гостя ВМ (install/remove/update) по SSH через hub.

    VM-аналог серверного `POST /servers/packages/bulk-action`, но per-VM.
    Крупноблочное право `(vm, vm_astra_update)` — деструктив над софтом гостя,
    той же силы, что обновление ОС; отдельного гранулярного права не заводим.
    Гейтит бронь и lifecycle-lock (`_load_vm_for_managed_op`, как astra_update),
    но своего lock'а не ставит — контракт как у серверной bulk-мутации
    (dispatch-and-poll). Guest-часть на managed-ВМ идёт по управляющему ключу —
    стэшим mgmt-материал (в payload едет только stash-ключ). Возвращает
    (vm, task_id). Валидацию `action`/имён пакетов делает endpoint.
    """
    task_kind, audit_action = _VM_PACKAGES_ACTION_MAP[action]
    vm, hub = await _load_vm_for_managed_op(
        db, identity, vm_id,
        action_perm=Action.VM_ASTRA_UPDATE,
        audit_action=audit_action, op=f"packages_{action}",
    )
    payload = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "operation": action,
        "packages": list(packages),
    }
    # IP гостя кладём хинтом, если известен (worker иначе резолвит через
    # virsh domifaddr); os_version помогает воркеру не гонять лишний детект.
    if vm.ip_address is not None:
        payload["guest_ip"] = str(vm.ip_address)
    stash_key = await stash_existing_vm_mgmt_creds(vm, audit_action)
    if stash_key:
        payload["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=task_kind, hub=hub, vm=vm,
        payload=payload, audit_action=audit_action, stash_key=stash_key,
    )
    await db.commit()
    audit_service.emit(
        audit_action, target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "operation": action,
            "package_count": len(packages),
            "department_id": vm.department_id,
        },
    )
    return vm, task_id


async def list_package_history(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    *,
    limit: int,
    offset: int,
):
    """История probe-запросов пакетов ВМ (DESC по времени). Право `(vm, view)`.

    Зеркало серверного `list_packages_history`: permission `(vm, view)` +
    видимость (cross-dept → 404) решаются здесь, дальше read идёт через
    `tasks_svc.list_vm_package_history` (cross-DB к dev_server_worker.tasks).
    Read-only, prepare/decommissioned-гейтов нет. Возвращает
    `(vm_id, total, items)`.
    """
    from src.services import tasks as tasks_svc

    audit_action = "vm.packages_history"
    with emit_denied_on_authz_error(
        audit_action, target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VIEW
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            audit_action, target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)

    items, total = await tasks_svc.list_vm_package_history(
        vm.id, limit=limit, offset=offset,
    )
    return vm.id, total, items


# ── inventory / OS-users sync (VM-аналоги серверных inventory.sync/users) ─────


async def _load_vm_for_guest_probe(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
    *,
    audit_action: str,
) -> tuple[Vm, object]:
    """Пролог guest-probe операций ВМ: право `(vm, vm_prepare)` + видимость + готовность.

    Инвентарь снимается по SSH через hub под управляющими кредами, поэтому ВМ
    обязана быть prepared (`is_managed`), иметь IP гостя и живой hub. Read-only —
    брони/lifecycle-lock не требует (как package-refresh). Возвращает (vm, hub).
    """
    with emit_denied_on_authz_error(
        audit_action, target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VM_PREPARE
        )
    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            audit_action, target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    if not vm.is_managed:
        raise ConflictError(
            error_code="VM_PREPARE_REQUIRED",
            message="VM is not prepared; run prepare before probing the guest",
        )
    if vm.ip_address is None:
        raise ConflictError(
            error_code="VM_GUEST_IP_UNKNOWN",
            message="VM guest IP is unknown; cannot reach the guest to probe it",
        )
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    return vm, hub


async def sync_inventory(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
) -> tuple[Vm, str]:
    """Диспатч `vm.inventory_sync` — снять hardware-inventory гостя ВМ по SSH через hub.

    VM-аналог серверного `inventory.sync`. Право `(vm, vm_prepare)`. ВМ обязана
    быть prepared, иметь IP гостя и живой hub. Probe идёт под управляющими
    кредами (в payload едет только stash-ключ). Возвращает (vm, task_id).
    """
    vm, hub = await _load_vm_for_guest_probe(
        db, identity, request, vm_id, audit_action="vm.inventory_sync",
    )
    payload = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "guest_ip": str(vm.ip_address),
    }
    stash_key = await stash_existing_vm_mgmt_creds(vm, "vm.inventory_sync")
    if stash_key:
        payload["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_INVENTORY_SYNC, hub=hub, vm=vm,
        payload=payload, audit_action="vm.inventory_sync", stash_key=stash_key,
    )
    await db.commit()
    audit_service.emit(
        "vm.inventory_sync", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "department_id": vm.department_id},
    )
    return vm, task_id


async def users_inventory(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
) -> tuple[Vm, str]:
    """Диспатч `vm.users_inventory` — снять OS-пользователей гостя ВМ по SSH через hub.

    VM-аналог серверного `users.inventory`. Право `(vm, vm_prepare)`. ВМ обязана
    быть prepared, иметь IP гостя и живой hub. Worker снимает getent-срез и сдаёт
    его callback'ом; server_service reconcile'ит привязанные учётки (warn-on-drift).
    Возвращает (vm, task_id).
    """
    vm, hub = await _load_vm_for_guest_probe(
        db, identity, request, vm_id, audit_action="vm.users_inventory",
    )
    payload = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "guest_ip": str(vm.ip_address),
    }
    stash_key = await stash_existing_vm_mgmt_creds(vm, "vm.users_inventory")
    if stash_key:
        payload["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_USERS_INVENTORY, hub=hub, vm=vm,
        payload=payload, audit_action="vm.users_inventory", stash_key=stash_key,
    )
    await db.commit()
    audit_service.emit(
        "vm.users_inventory", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "department_id": vm.department_id},
    )
    return vm, task_id


async def install_node_exporter(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    vm_id: str,
) -> tuple[Vm, str]:
    """Диспатч `vm.install_node_exporter` — поставить exporter в госте ВМ.

    Операция нужна для Grafana-панелей ВМ (`var-node=<guest_ip>:9100`). Идёт
    через hub под управляющими кредами ВМ, поэтому требует prepared ВМ,
    известный guest IP и живой hub. Право переиспользует prepare-поверхность:
    `(vm, vm_prepare)`.
    """
    with emit_denied_on_authz_error(
        "vm.node_exporter_installed", target_id=vm_id, target_type="vm",
        extra_details={"vm_id": vm_id}, identity=identity,
    ):
        await permissions.require_resource_action(
            db, identity, EntityType.VM, vm_id, Action.VM_PREPARE
        )

    vm = await repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.node_exporter_installed", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    await _ensure_visible(db, identity, vm)
    if not vm.is_managed:
        raise ConflictError(
            error_code="VM_PREPARE_REQUIRED",
            message="VM is not prepared; run prepare before installing node_exporter",
        )
    if vm.ip_address is None:
        raise ConflictError(
            error_code="VM_GUEST_IP_UNKNOWN",
            message="VM guest IP is unknown; cannot install node_exporter",
        )
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )

    payload = {
        **_hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "guest_ip": str(vm.ip_address),
    }
    stash_key = await stash_existing_vm_mgmt_creds(vm, "vm.node_exporter_installed")
    if stash_key:
        payload["creds_stash_key"] = stash_key
    task_id = await _dispatch_guest_task_with_stash(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VM_INSTALL_NODE_EXPORTER, hub=hub, vm=vm,
        payload=payload, audit_action="vm.node_exporter_installed",
        stash_key=stash_key,
    )
    await db.commit()
    audit_service.emit(
        "vm.node_exporter_installed", target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={"task_id": task_id, "department_id": vm.department_id},
    )
    return vm, task_id


# ── teardown VMS-hub (rm-vms-hub) ────────────────────────────────────────────


async def teardown_vms_hub(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    server_id: str,
) -> tuple[str, str, int]:
    """Снять сервер с роли VMS-hub. Право `(vm, vms_hub_prepare)` (привилегированное).

    Симметрия старому rm-vms-hub: карточки ВМ отдела на hub'е сносятся из БД
    сразу (диски/снимки — каскадом), hub → free (`is_vms_hub=False`), и воркеру
    диспатчится `vms_hub.teardown` для очистки хоста (домены/пул/пакеты).
    Возвращает (server_id, task_id, снесённых ВМ).
    """
    with emit_denied_on_authz_error(
        "vms_hub.torn_down", target_id=server_id, target_type="server",
        extra_details={"server_id": server_id}, identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.VM, Action.VMS_HUB_PREPARE)
    hub = await server_repo.get_by_id(db, server_id)
    if hub is None or hub.department_id != identity.department_id:
        audit_service.emit(
            "vms_hub.torn_down", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    if not hub.is_vms_hub:
        audit_service.emit(
            "vms_hub.torn_down", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "not_a_vms_hub"},
        )
        raise ConflictError(
            error_code="NOT_A_VMS_HUB",
            message="Server is not a VMS-hub; nothing to tear down",
        )
    vms = await repo.list_for_hub_department(db, hub.id, identity.department_id)
    # Имена доменов собираем ДО удаления карточек — воркеру нужен список ВМ,
    # которые он снесёт на хосте (destroy/undefine). os_family и пул совпадают
    # с дефолтами hub-подготовки (Astra → apt, пул образов `/vms`).
    payload = {
        **_hub_payload(hub),
        "phy_if": hub.network_interface_name,
        "os_family": VMS_HUB_OS_FAMILY,
        "storage_pool_path": VMS_HUB_POOL_PATH,
        "vms": [vm.name for vm in vms],
    }
    task_id = await _dispatch_vm_task(
        db=db, identity=identity, request=request,
        task_kind=VmTaskKind.VMS_HUB_TEARDOWN, hub=hub, vm=None,
        payload=payload, audit_action="vms_hub.torn_down",
    )
    for vm in vms:
        await repo.delete(db, vm)
    hub.is_vms_hub = False
    hub.vms_hub_prepared_at = None
    await db.commit()
    audit_service.emit(
        "vms_hub.torn_down", target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={"task_id": task_id, "vms_removed": len(vms), "department_id": hub.department_id},
    )
    return server_id, task_id, len(vms)
