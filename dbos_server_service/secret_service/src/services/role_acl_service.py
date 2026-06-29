"""Use cases для /credentials/{id}/acl — RoleACL CRUD.

Для cross_department-кред'ы выдача ACL в recipient_dep требует существующего
DeptGrant — иначе 422 DEPT_GRANT_REQUIRED (см. README §«DeptGrant»).
"""

from __future__ import annotations

import logging
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
from src.schemas.role_acls import RoleACLCreate, RoleACLUpsert
from src.services import audit_service
from src.services.credential_service import load_for_action

logger = logging.getLogger(__name__)


def _emit_failure(
    action: str,
    *,
    target_id: str | None,
    target_type: str,
    exc: Exception,
    extra: dict | None = None,
) -> None:
    """Emit failure-вариант action'а; ошибки audit-канала не пропагирует."""
    details: dict = {"error_class": type(exc).__name__}
    error_code = getattr(exc, "error_code", None)
    if error_code:
        details["error_code"] = error_code
    if extra:
        details.update(extra)
    try:
        audit_service.emit(
            action,
            target_id=target_id,
            target_type=target_type,
            status="failure",
            allowed=False,
            details=details,
        )
    except Exception as emit_exc:  # noqa: BLE001
        logger.warning(
            "audit_emit_failed action=%s err=%s", action, type(emit_exc).__name__
        )


def _new_acl_id() -> str:
    return f"acl_{_secrets.token_hex(16)}"


def _normalize_flags(
    *, can_view: bool, can_read: bool, can_write: bool
) -> tuple[bool, bool, bool]:
    """Подтянуть младшие уровни лесенки под выданный старший.

    view ⊂ read ⊂ write: write влечёт read, read влечёт view. Храним флаги уже
    выровненными, чтобы листинг ACL и бэкфилл-инвариант совпадали; саму
    проверку доступа access_service всё равно делает по лесенке.
    """
    can_read = can_read or can_write
    can_view = can_view or can_read
    return can_view, can_read, can_write


async def _check_dept_target(db, identity, cred, dept_id: str) -> None:
    """Проверить, что ACL для (cred, dept_id) допустим по scope'у.

    personal — только dep владельца; cross_department recipient — только при
    наличии DeptGrant. Бросает DomainValidationError при нарушении.
    """
    # personal: ACL только в dep'е владельца. Иначе non-owner из чужого dep'а
    # получит ACL'ную дыру в обход cross-dep flow.
    if cred.scope == "personal":
        owner_dept = cred.owner_user_dept_id or identity.department_id
        if dept_id != owner_dept:
            raise DomainValidationError(
                error_code="PERSONAL_ACL_OWNER_DEPT_ONLY",
                message=(
                    "personal credential ACL must be granted in the owner's "
                    "department only"
                ),
                details={"dept_id": dept_id, "owner_dept_id": owner_dept},
            )

    # cross_dep: если ACL выдаётся НЕ owner_dep'у — нужен DeptGrant.
    if cred.scope == "cross_department" and dept_id != cred.owner_dept_id:
        has_grant = await dept_grants_repo.exists_for(db, cred.id, dept_id)
        if not has_grant:
            raise DomainValidationError(
                error_code="DEPT_GRANT_REQUIRED",
                message=(
                    "cross_department credential requires DeptGrant for "
                    "recipient_dept before issuing RoleACL"
                ),
                details={"dept_id": dept_id},
            )


async def add(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: RoleACLCreate,
) -> RoleACL:
    """Создать ACL. cross_dep recipient требует DeptGrant.

    На failure-пути эмитим `tokens.role_acl_added / failure` до raise.
    """
    try:
        cred = await load_for_action(db, identity, cred_id, "grant_acl")

        await _check_dept_target(db, identity, cred, payload.dept_id)

        # Friendly UNIQUE pre-check
        existing = await repo.find(db, cred.id, payload.dept_id, payload.role_name)
        if existing is not None:
            raise ConflictError(
                error_code="ROLE_ACL_DUPLICATE",
                message="RoleACL with this (dept_id, role_name) already exists",
            )

        can_view, can_read, can_write = _normalize_flags(
            can_view=payload.can_view,
            can_read=payload.can_read,
            can_write=payload.can_write,
        )
        try:
            acl = await repo.create(
                db,
                id=_new_acl_id(),
                cred_id=cred.id,
                dept_id=payload.dept_id,
                role_name=payload.role_name,
                can_view=can_view,
                can_read=can_read,
                can_write=can_write,
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
    except Exception as exc:
        _emit_failure(
            "tokens.role_acl_added",
            target_id=cred_id,
            target_type="credential",
            exc=exc,
            extra={
                "dept_id": payload.dept_id,
                "role_name": payload.role_name,
            },
        )
        raise

    audit_service.emit(
        "tokens.role_acl_added",
        target_id=acl.id,
        target_type="role_acl",
        details={
            "cred_id": cred.id,
            "dept_id": acl.dept_id,
            "role_name": acl.role_name,
            "can_view": acl.can_view,
            "can_read": acl.can_read,
            "can_write": acl.can_write,
        },
    )
    return acl


async def upsert(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: RoleACLUpsert,
) -> RoleACL | None:
    """Идемпотентно задать пару флагов для (cred, dept, role).

    Строки нет — создаём; есть — переписываем флаги; оба флага false — снимаем
    строку (для UI «нет доступа» = отсутствие записи, как и читает матрица).
    Возвращает обновлённый/созданный ACL либо None, если строка снята.

    Аудит: создание/обновление → `tokens.role_acl_added`; снятие →
    `tokens.role_acl_revoked`. На failure-пути эмитим failure-вариант
    `tokens.role_acl_added` до raise.
    """
    can_view, can_read, can_write = _normalize_flags(
        can_view=payload.can_view,
        can_read=payload.can_read,
        can_write=payload.can_write,
    )
    all_false = not can_view and not can_read and not can_write
    try:
        cred = await load_for_action(db, identity, cred_id, "grant_acl")
        await _check_dept_target(db, identity, cred, payload.dept_id)

        existing = await repo.find(db, cred.id, payload.dept_id, payload.role_name)

        if all_false:
            if existing is None:
                # Нечего снимать — идемпотентный no-op, событий не пишем.
                # До этой ветки шли только SELECT'ы (load_for_action /
                # _check_dept_target / find), ничего не меняли — откатывать
                # нечего, а лишний rollback рвёт внешнюю транзакцию вызова.
                return None
            removed_id = existing.id
            await repo.delete(db, existing)
            await db.commit()
            audit_service.emit(
                "tokens.role_acl_revoked",
                target_id=removed_id,
                target_type="role_acl",
                details={
                    "cred_id": cred.id,
                    "dept_id": payload.dept_id,
                    "role_name": payload.role_name,
                },
            )
            return None

        if existing is not None:
            acl = await repo.update_flags(
                db,
                existing,
                can_view=can_view,
                can_read=can_read,
                can_write=can_write,
            )
        else:
            acl = await repo.create(
                db,
                id=_new_acl_id(),
                cred_id=cred.id,
                dept_id=payload.dept_id,
                role_name=payload.role_name,
                can_view=can_view,
                can_read=can_read,
                can_write=can_write,
                granted_by_user_id=identity.user_id,
            )
        await db.commit()
        await db.refresh(acl)
    except Exception as exc:
        _emit_failure(
            "tokens.role_acl_added",
            target_id=cred_id,
            target_type="credential",
            exc=exc,
            extra={
                "dept_id": payload.dept_id,
                "role_name": payload.role_name,
            },
        )
        raise

    audit_service.emit(
        "tokens.role_acl_added",
        target_id=acl.id,
        target_type="role_acl",
        details={
            "cred_id": cred.id,
            "dept_id": acl.dept_id,
            "role_name": acl.role_name,
            "can_view": acl.can_view,
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
    """Снять ACL. Требует grant_acl.

    На failure-пути эмитим `tokens.role_acl_revoked / failure` до raise.
    """
    try:
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
    except Exception as exc:
        _emit_failure(
            "tokens.role_acl_revoked",
            target_id=acl_id,
            target_type="role_acl",
            exc=exc,
            extra={"cred_id": cred_id},
        )
        raise

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
