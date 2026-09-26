"""Профили подготовки стенда и шаг настройки стенда.

* CRUD профилей: чтение — свой отдел (видны профили отдела и общие); запись —
  `provisioning_profile:update` или department_admin отдела, общий профиль —
  только по матрице.
* `effective_values` — значения для `prepare-for-test` (,
  `provisioning`): профиль теста → профиль отдела по умолчанию → общий.
* `resolve_stand_setup` — шаг настройки стенда теста для C2: скрипт
  рендерится подстановками `{{CODE}}` (как `starter.sh` профиля запуска),
  `script_is_sensitive` — если подставлена sensitive-переменная.
"""

from __future__ import annotations

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import ProvisioningProfile, TestDefinition
from src.schemas.provisioning_profile import ProvisioningProfileCreate, ProvisioningProfileUpdate
from src.services import audit_service, permissions
from src.utils.ids import provisioning_profile_id as new_id

_VALUE_FIELDS = (
    "allowed_failed_units", "degraded_reboot_attempts",
    "disable_pam_lastlog_inactive", "boot_wait_timeout_seconds",
)


def _dept_cond(department_id: str | None):
    column = ProvisioningProfile.department_id
    return column.is_(None) if department_id is None else column == department_id


async def _get_default(db: AsyncSession, department_id: str | None) -> ProvisioningProfile | None:
    stmt = select(ProvisioningProfile).where(_dept_cond(department_id), ProvisioningProfile.is_default.is_(True))
    return (await db.execute(stmt)).scalar_one_or_none()


def values_of(profile: ProvisioningProfile) -> dict:
    return {field: getattr(profile, field) for field in _VALUE_FIELDS}


async def effective_values(db: AsyncSession, test: TestDefinition, department_id: str | None) -> dict | None:
    """`provisioning` для prepare-for-test; `None` — профилей нет вовсе."""
    return await effective_values_for(db, test.provisioning_profile_id, department_id)


async def effective_values_for(db: AsyncSession, profile_id: str | None, department_id: str | None) -> dict | None:
    """То же по id профиля (стенд сценария): профиль → профиль отдела → общий."""
    profile = None
    if profile_id:
        profile = await db.get(ProvisioningProfile, profile_id)
    if profile is None and department_id is not None:
        profile = await _get_default(db, department_id)
    if profile is None:
        profile = await _get_default(db, None)
    return values_of(profile) if profile is not None else None


async def check_assignable(db: AsyncSession, profile_id: str | None, department_id: str | None) -> None:
    """Профиль теста должен быть общим или профилем отдела теста."""
    if not profile_id:
        return
    profile = await db.get(ProvisioningProfile, profile_id)
    if profile is None or (profile.department_id is not None and profile.department_id != department_id):
        raise DomainValidationError(
            error_code="PROVISIONING_PROFILE_INVALID",
            message="Provisioning profile not found or belongs to another department",
            details={"provisioning_profile_id": profile_id},
        )


def stand_setup_is_empty(setup: dict | None) -> bool:
    return not setup or (not setup.get("kernel_cmdline_extra") and not (setup.get("script") or "").strip())


async def resolve_stand_setup(ctx, setup: dict | None) -> dict | None:
    """Шаг настройки стенда для C2 с отрезолвленным скриптом; пустой — `None`."""
    if stand_setup_is_empty(setup):
        return None
    from src.services import launch_profile  # поздний импорт: launch_profile тянет queue-зависимости

    rendered = await launch_profile.render_shell(ctx, setup.get("script") or "")
    reboot_after = setup.get("reboot_after")
    return {
        "kernel_cmdline_extra": list(setup.get("kernel_cmdline_extra") or []),
        "script": rendered.value,
        "script_is_sensitive": rendered.sensitive,
        "run_as": setup.get("run_as") or "root",
        "phase": setup.get("phase") or "after_boot",
        "reboot_after": True if reboot_after is None else bool(reboot_after),
        "timeout_seconds": int(setup.get("timeout_seconds") or 1800),
    }


# ── API ──────────────────────────────────────────────────────────────────────

async def _require_update(db: AsyncSession, identity: Identity, department_id: str | None, action: str) -> None:
    try:
        if department_id is None:
            await permissions.require_action(db, identity, EntityType.PROVISIONING_PROFILE, Action.UPDATE)
        else:
            await permissions.require_department_action(
                db, identity, department_id, EntityType.PROVISIONING_PROFILE, Action.UPDATE,
            )
    except AuthorizationError:
        audit_service.emit(
            action, target_id=department_id or "global", target_type="provisioning_profile",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise


async def list_for(db: AsyncSession, identity: Identity, department_id: str) -> list[ProvisioningProfile]:
    permissions.require_own_department(identity, department_id)
    stmt = select(ProvisioningProfile).where(
        or_(ProvisioningProfile.department_id.is_(None), ProvisioningProfile.department_id == department_id),
    ).order_by(ProvisioningProfile.department_id.is_(None), ProvisioningProfile.name)
    return list((await db.execute(stmt)).scalars().all())


async def _clear_default(db: AsyncSession, department_id: str | None, except_id: str) -> None:
    await db.execute(
        update(ProvisioningProfile)
        .where(_dept_cond(department_id), ProvisioningProfile.id != except_id)
        .values(is_default=False)
    )


async def create(db: AsyncSession, identity: Identity, payload: ProvisioningProfileCreate) -> ProvisioningProfile:
    await _require_update(db, identity, payload.department_id, "provisioning_profile.create")
    profile = ProvisioningProfile(
        id=new_id(), created_by=identity.user_id, **payload.model_dump(),
    )
    db.add(profile)
    await db.flush()
    if payload.is_default:
        await _clear_default(db, payload.department_id, profile.id)
    await db.commit()
    await db.refresh(profile)
    audit_service.emit(
        "provisioning_profile.create", target_id=profile.id, target_type="provisioning_profile",
        status="success", allowed=True,
        details={"department_id": profile.department_id, **values_of(profile)},
    )
    return profile


async def update_profile(
    db: AsyncSession, identity: Identity, profile_id: str, payload: ProvisioningProfileUpdate,
) -> ProvisioningProfile:
    profile = await db.get(ProvisioningProfile, profile_id)
    if profile is None:
        raise NotFoundError(error_code="PROVISIONING_PROFILE_NOT_FOUND", message="Provisioning profile not found")
    if profile.department_id is not None:
        permissions.require_own_department(identity, profile.department_id)
    await _require_update(db, identity, profile.department_id, "provisioning_profile.update")
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k == "boot_wait_timeout_seconds"}
    for key, value in changes.items():
        setattr(profile, key, value)
    if changes.get("is_default"):
        await _clear_default(db, profile.department_id, profile.id)
    await db.commit()
    await db.refresh(profile)
    audit_service.emit(
        "provisioning_profile.update", target_id=profile.id, target_type="provisioning_profile",
        status="success", allowed=True, details={"fields": sorted(changes)},
    )
    return profile
