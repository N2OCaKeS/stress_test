"""Use cases для пресетов стандартных ВМ (vm_preset, §Пресеты дизайна).

CRUD шаблонов «типовых» ВМ отдела. Право на всё — `(vm, vm_preset_manage)`
(тип-wide, dep_admin / service-admin). Разворачивание пресетов на hub'е живёт в
`services.vm.create_default_vms` (право `(vm, create)`).
"""

import logging

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.models import VmPreset
from src.repositories import vm_preset as repo
from src.schemas.identity import IdentityContext
from src.schemas.vm import VmPresetCreate, VmPresetUpdate
from src.services import audit_service, permissions
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import vm_preset_id as new_preset_id

logger = logging.getLogger(__name__)


async def list_presets(
    db: AsyncSession, identity: IdentityContext, *, limit: int, offset: int,
) -> tuple[list[VmPreset], int]:
    """Список пресетов своего отдела. Право `(vm, vm_preset_manage)`."""
    await permissions.require_action(
        db, identity, EntityType.VM, Action.VM_PRESET_MANAGE
    )
    if identity.department_id is None:
        return [], 0
    items = await repo.list_in_department(
        db, identity.department_id, limit=limit, offset=offset
    )
    total = await repo.count_in_department(db, identity.department_id)
    return items, total


async def get_preset(
    db: AsyncSession, identity: IdentityContext, preset_id: str,
) -> VmPreset:
    """Карточка пресета. Право `(vm, vm_preset_manage)` + изоляция отдела (cross → 404)."""
    await permissions.require_action(
        db, identity, EntityType.VM, Action.VM_PRESET_MANAGE
    )
    preset = await repo.get_by_id(db, preset_id)
    if preset is None or preset.department_id != identity.department_id:
        raise NotFoundError(error_code="VM_PRESET_NOT_FOUND", message="VM preset not found")
    return preset


async def create_preset(
    db: AsyncSession, identity: IdentityContext, request: Request, payload: VmPresetCreate,
) -> VmPreset:
    """INSERT пресета. Право `(vm, vm_preset_manage)`, изоляция отдела."""
    with emit_denied_on_authz_error(
        "vm_preset.created", target_type="vm_preset",
        extra_details={"department_id": payload.department_id}, identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.VM, Action.VM_PRESET_MANAGE
        )
    if payload.department_id != identity.department_id:
        audit_service.emit(
            "vm_preset.created", target_type="vm_preset", status="failure", allowed=True,
            details={"reason": "department_isolation", "department_id": payload.department_id},
        )
        raise ConflictError(
            error_code="DEPARTMENT_ISOLATION",
            message="Cannot create a VM preset in a different department",
        )
    data = {
        "id": new_preset_id(),
        "name": payload.name,
        "department_id": payload.department_id,
        "box": payload.box,
        "os_version": payload.os_version,
        "cpu": payload.cpu,
        "ram_mb": payload.ram_mb,
        "disk_gb": payload.disk_gb,
        "network_mode": payload.network_mode.value,
        "fixed_ip": str(payload.fixed_ip) if payload.fixed_ip is not None else None,
        "number": payload.number,
        "created_by": identity.user_id,
    }
    try:
        preset = await repo.create(db, data)
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "vm_preset.created", target_type="vm_preset", status="failure", allowed=True,
            details={"reason": "duplicate", "name": payload.name},
        )
        raise ConflictError(
            error_code="VM_PRESET_DUPLICATE",
            message="A VM preset with this name already exists in the department",
        ) from exc
    await db.commit()
    await db.refresh(preset)
    audit_service.emit(
        "vm_preset.created", target_id=preset.id, target_type="vm_preset",
        status="success", allowed=True,
        details={"name": preset.name, "network_mode": preset.network_mode, "department_id": preset.department_id},
    )
    return preset


async def update_preset(
    db: AsyncSession, identity: IdentityContext, request: Request,
    preset_id: str, payload: VmPresetUpdate,
) -> VmPreset:
    """PATCH пресета (частично). Право `(vm, vm_preset_manage)`."""
    preset = await get_preset(db, identity, preset_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise DomainValidationError(
            error_code="VM_PRESET_UPDATE_EMPTY", message="no fields to update",
        )
    for key, value in fields.items():
        if key == "network_mode" and value is not None:
            value = value.value if hasattr(value, "value") else value
        if key == "fixed_ip" and value is not None:
            value = str(value)
        setattr(preset, key, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            error_code="VM_PRESET_DUPLICATE",
            message="A VM preset with this name already exists in the department",
        ) from exc
    await db.refresh(preset)
    audit_service.emit(
        "vm_preset.updated", target_id=preset.id, target_type="vm_preset",
        status="success", allowed=True,
        details={"changed": sorted(fields.keys()), "department_id": preset.department_id},
    )
    return preset


async def delete_preset(
    db: AsyncSession, identity: IdentityContext, preset_id: str,
) -> None:
    """DELETE пресета. Право `(vm, vm_preset_manage)` + изоляция отдела."""
    preset = await get_preset(db, identity, preset_id)
    dept = preset.department_id
    name = preset.name
    await repo.delete(db, preset)
    await db.commit()
    audit_service.emit(
        "vm_preset.deleted", target_id=preset_id, target_type="vm_preset",
        status="success", allowed=True,
        details={"name": name, "department_id": dept},
    )
