"""Use cases для /credentials/{id}/acl — RoleACL CRUD.

Для cross_department-кред'ы выдача ACL в recipient_dep требует существующего
DeptGrant — иначе 422 DEPT_GRANT_REQUIRED (см. README §«DeptGrant»).
"""

from __future__ import annotations

import secrets as _secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.models import RoleACL
from src.repositories import dept_grants as dept_grants_repo
from src.repositories import role_acls as repo
from src.schemas.role_acls import RoleACLCreate
from src.services import audit_service
from src.services.credential_service import load_for_action


def _new_acl_id() -> str:
    return f"acl_{_secrets.token_hex(16)}"


async def add(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: RoleACLCreate,
) -> RoleACL:
    """Создать ACL. cross_dep recipient требует DeptGrant."""
    cred = await load_for_action(db, identity, cred_id, "grant_acl")

    # personal: ACL только в dep'е владельца. Иначе non-owner из чужого dep'а
    # получит ACL'ную дыру в обход cross-dep flow.
    if cred.scope == "personal":
        owner_dept = cred.owner_user_dept_id or identity.department_id
        if payload.dept_id != owner_dept:
            raise DomainValidationError(
                error_code="PERSONAL_ACL_OWNER_DEPT_ONLY",
                message=(
                    "personal credential ACL must be granted in the owner's "
                    "department only"
                ),
                details={"dept_id": payload.dept_id, "owner_dept_id": owner_dept},
            )

    # cross_dep: если ACL выдаётся НЕ owner_dep'у — нужен DeptGrant.
    if cred.scope == "cross_department" and payload.dept_id != cred.owner_dept_id:
        has_grant = await dept_grants_repo.exists_for(db, cred.id, payload.dept_id)
        if not has_grant:
            raise DomainValidationError(
                error_code="DEPT_GRANT_REQUIRED",
                message=(
                    "cross_department credential requires DeptGrant for "
                    "recipient_dept before issuing RoleACL"
                ),
                details={"dept_id": payload.dept_id},
            )

    # Friendly UNIQUE pre-check
    existing = await repo.find(db, cred.id, payload.dept_id, payload.role_name)
    if existing is not None:
        raise ConflictError(
            error_code="ROLE_ACL_DUPLICATE",
            message="RoleACL with this (dept_id, role_name) already exists",
        )

    try:
        acl = await repo.create(
            db,
            id=_new_acl_id(),
            cred_id=cred.id,
            dept_id=payload.dept_id,
            role_name=payload.role_name,
            can_read=payload.can_read,
            can_write=payload.can_write,
            granted_by_user_id=identity.user_id,
        )
        await db.commit()
        await db.refresh(acl)
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            error_code="ROLE_ACL_DUPLICATE",
            message="RoleACL conflict (race)",
        ) from exc

    audit_service.emit(
        "tokens.role_acl_added",
        target_id=acl.id,
        target_type="role_acl",
        details={
            "cred_id": cred.id,
            "dept_id": acl.dept_id,
            "role_name": acl.role_name,
            "can_read": acl.can_read,
            "can_write": acl.can_write,
        },
    )
    return acl


async def list_for(
    db: AsyncSession, identity: Identity, cred_id: str
) -> list[RoleACL]:
    """Список ACL'ей кред'ы. Доступ — как у read (управление видно reader+)."""
    cred = await load_for_action(db, identity, cred_id, "read")
    return await repo.get_for_cred(db, cred.id)


async def revoke(
    db: AsyncSession, identity: Identity, cred_id: str, acl_id: str
) -> None:
    """Снять ACL. Требует grant_acl."""
    cred = await load_for_action(db, identity, cred_id, "grant_acl")

    acls = await repo.get_for_cred(db, cred.id)
    target = next((a for a in acls if a.id == acl_id), None)
    if target is None:
        raise NotFoundError(
            error_code="ROLE_ACL_NOT_FOUND",
            message="RoleACL not found for this credential",
        )

    # Для cross_dep recipient'а revoke ACL'я разрешён только локальному
    # dep_admin'у этого dep'а. owner-side dep_admin тоже может (он мощнее).
    if (
        cred.scope == "cross_department"
        and target.dept_id != cred.owner_dept_id
    ):
        # Локальный recipient dep_admin: identity.department_id == target.dept_id.
        # Owner dep_admin: identity.department_id == cred.owner_dept_id (он
        # удовлетворил check_access(grant_acl) выше — это путь scope=cross →
        # owner-dep с dep_admin).
        # Здесь добавочной проверки нет: check_access уже разрулил.
        pass

    target_id = target.id
    target_dept = target.dept_id
    target_role = target.role_name

    await repo.delete(db, target)
    await db.commit()

    audit_service.emit(
        "tokens.role_acl_revoked",
        target_id=target_id,
        target_type="role_acl",
        details={
            "cred_id": cred.id,
            "dept_id": target_dept,
            "role_name": target_role,
        },
    )
