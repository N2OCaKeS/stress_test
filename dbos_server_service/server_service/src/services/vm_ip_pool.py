"""Use cases для пулов IP-адресов ВМ (IPAM, §8 дизайна).

CRUD пулов + аллокатор свободных адресов. Право на всё — `(vm, vm_net_manage)`
(тип-wide, dep_admin / service-admin). Учёт занятости — по БД: свободный адрес =
адрес диапазона за вычетом gateway и уже назначенных `vms.ip_address` отдела.
"""

import logging
from ipaddress import ip_address, ip_network

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.models import VmIpPool
from src.repositories import vm as vm_repo
from src.repositories import vm_ip_pool as repo
from src.schemas.identity import IdentityContext
from src.schemas.vm import VmIpPoolCreate, VmIpPoolUpdate
from src.services import audit_service, permissions
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import vm_ip_pool_id as new_pool_id

logger = logging.getLogger(__name__)

# Верхний предел сканирования диапазона: не материализуем гигантские подсети.
_SCAN_CAP = 65536
# Сколько свободных адресов максимум отдаём списком в available-ips.
_AVAILABLE_LIST_CAP = 256


def _validate_range(cidr: str, start: str, end: str, gateway: str | None) -> None:
    """Проверить, что диапазон и gateway лежат в cidr и start ≤ end (иначе 422)."""
    try:
        net = ip_network(cidr, strict=False)
        s = ip_address(start)
        e = ip_address(end)
    except ValueError as exc:
        raise DomainValidationError(
            error_code="VM_IP_POOL_INVALID",
            message=f"invalid cidr/range: {exc}",
        ) from exc
    if s.version != net.version or e.version != net.version:
        raise DomainValidationError(
            error_code="VM_IP_POOL_INVALID",
            message="range address family does not match the pool cidr",
        )
    if int(s) > int(e):
        raise DomainValidationError(
            error_code="VM_IP_POOL_INVALID",
            message="range_start must be less than or equal to range_end",
        )
    if s not in net or e not in net:
        raise DomainValidationError(
            error_code="VM_IP_POOL_INVALID",
            message="range_start / range_end must belong to the pool cidr",
        )
    if gateway is not None and ip_address(gateway) not in net:
        raise DomainValidationError(
            error_code="VM_IP_POOL_INVALID",
            message="gateway must belong to the pool cidr",
        )


def _iter_range(pool: VmIpPool):
    """Итератор адресов диапазона пула (строки), капнутый `_SCAN_CAP`."""
    start = int(ip_address(str(pool.range_start)))
    end = int(ip_address(str(pool.range_end)))
    count = 0
    for value in range(start, end + 1):
        if count >= _SCAN_CAP:
            break
        count += 1
        yield str(ip_address(value))


def free_ips(pool: VmIpPool, used: set[str]) -> tuple[list[str], int]:
    """Свободные адреса диапазона пула. Возвращает (list-cap, total_free).

    Из диапазона исключаются gateway и занятые (`used`). `list` капнут
    `_AVAILABLE_LIST_CAP`, `total_free` считает всё (в пределах scan-cap'а).
    """
    reserved = set(used)
    if pool.gateway is not None:
        reserved.add(str(pool.gateway))
    listed: list[str] = []
    total = 0
    for addr in _iter_range(pool):
        if addr in reserved:
            continue
        total += 1
        if len(listed) < _AVAILABLE_LIST_CAP:
            listed.append(addr)
    return listed, total


def allocate_ip(pool: VmIpPool, used: set[str]) -> str:
    """Выбрать первый свободный адрес пула. Пул исчерпан → 409 VM_IP_POOL_EXHAUSTED."""
    listed, _ = free_ips(pool, used)
    if not listed:
        raise ConflictError(
            error_code="VM_IP_POOL_EXHAUSTED",
            message="No free IP addresses left in the pool",
            details={"pool_id": pool.id},
        )
    return listed[0]


def ip_in_pool(pool: VmIpPool, ip: str) -> bool:
    """True, если `ip` принадлежит диапазону пула (для валидации заданного IP)."""
    value = int(ip_address(ip))
    return int(ip_address(str(pool.range_start))) <= value <= int(
        ip_address(str(pool.range_end))
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────


async def list_pools(
    db: AsyncSession, identity: IdentityContext, *, limit: int, offset: int,
) -> tuple[list[VmIpPool], int]:
    """Список пулов своего отдела. Право `(vm, vm_net_manage)`."""
    await permissions.require_action(db, identity, EntityType.VM, Action.VM_NET_MANAGE)
    if identity.department_id is None:
        return [], 0
    items = await repo.list_in_department(
        db, identity.department_id, limit=limit, offset=offset
    )
    total = await repo.count_in_department(db, identity.department_id)
    return items, total


async def get_pool(
    db: AsyncSession, identity: IdentityContext, pool_id: str,
) -> VmIpPool:
    """Карточка пула. Право `(vm, vm_net_manage)` + изоляция отдела (cross → 404)."""
    await permissions.require_action(db, identity, EntityType.VM, Action.VM_NET_MANAGE)
    pool = await repo.get_by_id(db, pool_id)
    if pool is None or pool.department_id != identity.department_id:
        raise NotFoundError(error_code="VM_IP_POOL_NOT_FOUND", message="IP pool not found")
    return pool


async def create_pool(
    db: AsyncSession, identity: IdentityContext, request: Request, payload: VmIpPoolCreate,
) -> VmIpPool:
    """INSERT пула. Право `(vm, vm_net_manage)`, изоляция отдела, валидация диапазона."""
    with emit_denied_on_authz_error(
        "vm_ip_pool.created", target_type="vm_ip_pool",
        extra_details={"department_id": payload.department_id}, identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.VM, Action.VM_NET_MANAGE
        )
    if payload.department_id != identity.department_id:
        audit_service.emit(
            "vm_ip_pool.created", target_type="vm_ip_pool", status="failure", allowed=True,
            details={"reason": "department_isolation", "department_id": payload.department_id},
        )
        raise ConflictError(
            error_code="DEPARTMENT_ISOLATION",
            message="Cannot create an IP pool in a different department",
        )
    _validate_range(
        payload.cidr, str(payload.range_start), str(payload.range_end),
        str(payload.gateway) if payload.gateway is not None else None,
    )
    data = {
        "id": new_pool_id(),
        "name": payload.name,
        "department_id": payload.department_id,
        "cidr": payload.cidr,
        "gateway": str(payload.gateway) if payload.gateway is not None else None,
        "netmask": payload.netmask,
        "dns": list(payload.dns),
        "range_start": str(payload.range_start),
        "range_end": str(payload.range_end),
        "server_id": payload.server_id,
        "created_by": identity.user_id,
    }
    try:
        pool = await repo.create(db, data)
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "vm_ip_pool.created", target_type="vm_ip_pool", status="failure", allowed=True,
            details={"reason": "duplicate", "name": payload.name},
        )
        raise ConflictError(
            error_code="VM_IP_POOL_DUPLICATE",
            message="An IP pool with this name already exists in the department",
        ) from exc
    await db.commit()
    await db.refresh(pool)
    audit_service.emit(
        "vm_ip_pool.created", target_id=pool.id, target_type="vm_ip_pool",
        status="success", allowed=True,
        details={"name": pool.name, "cidr": pool.cidr, "department_id": pool.department_id},
    )
    return pool


async def update_pool(
    db: AsyncSession, identity: IdentityContext, request: Request,
    pool_id: str, payload: VmIpPoolUpdate,
) -> VmIpPool:
    """PATCH пула (частично). Право `(vm, vm_net_manage)`, ревалидация диапазона."""
    pool = await get_pool(db, identity, pool_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise DomainValidationError(
            error_code="VM_IP_POOL_UPDATE_EMPTY", message="no fields to update",
        )
    for key, value in fields.items():
        if key in {"gateway", "range_start", "range_end"} and value is not None:
            value = str(value)
        if key == "dns" and value is not None:
            value = list(value)
        setattr(pool, key, value)
    _validate_range(
        pool.cidr, str(pool.range_start), str(pool.range_end),
        str(pool.gateway) if pool.gateway is not None else None,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            error_code="VM_IP_POOL_DUPLICATE",
            message="An IP pool with this name already exists in the department",
        ) from exc
    await db.refresh(pool)
    audit_service.emit(
        "vm_ip_pool.updated", target_id=pool.id, target_type="vm_ip_pool",
        status="success", allowed=True,
        details={"changed": sorted(fields.keys()), "department_id": pool.department_id},
    )
    return pool


async def delete_pool(
    db: AsyncSession, identity: IdentityContext, pool_id: str,
) -> None:
    """DELETE пула. Право `(vm, vm_net_manage)` + изоляция отдела."""
    pool = await get_pool(db, identity, pool_id)
    dept = pool.department_id
    name = pool.name
    await repo.delete(db, pool)
    await db.commit()
    audit_service.emit(
        "vm_ip_pool.deleted", target_id=pool_id, target_type="vm_ip_pool",
        status="success", allowed=True,
        details={"name": name, "department_id": dept},
    )


async def available_ips(
    db: AsyncSession, identity: IdentityContext, pool_id: str,
) -> dict:
    """Свободные адреса пула (аллокатор в режиме read). Право `(vm, vm_net_manage)`."""
    pool = await get_pool(db, identity, pool_id)
    used = await vm_repo.list_used_ips(db, pool.department_id)
    listed, total = free_ips(pool, used)
    return {"pool_id": pool.id, "available": listed, "total_free": total}
