"""Use cases для /credentials/{id}/dept-grants (только cross_department).

DeptGrant — owner-side операция: его выдаёт dep_admin владеющего dep'а или
admin `secret_service` того же dep'а (per-(dept, service) роль, cross-dept
привилегий не даёт). Revoke каскадно сносит все RoleACL'и
(cred_id, recipient_dept).
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
from src.models import DeptGrant
from src.repositories import dept_grants as repo
from src.repositories import role_acls as acl_repo
from src.schemas.dept_grants import DeptGrantCreate
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


def _new_grant_id() -> str:
    return f"dgr_{_secrets.token_hex(16)}"


def _ensure_cross_dep_scope(cred) -> None:
    if cred.scope != "cross_department":
        raise DomainValidationError(
            error_code="DEPT_GRANT_NOT_APPLICABLE",
            message="DeptGrant is only applicable to cross_department credentials",
        )


async def add(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: DeptGrantCreate,
) -> DeptGrant:
    """Создать DeptGrant. Только owner dep_admin или admin secret_service владеющего dep'а.

    На любой ошибке (denied/duplicate/integrity/scope-mismatch) эмитим
    `tokens.dept_grant_added / failure` до raise.
    """
    try:
        cred = await load_for_action(db, identity, cred_id, "grant_dept")
        _ensure_cross_dep_scope(cred)

        if payload.recipient_dept_id == cred.owner_dept_id:
            raise DomainValidationError(
                error_code="DEPT_GRANT_RECIPIENT_IS_OWNER",
                message="Recipient_dept_id must differ from owner_dept_id",
            )

        existing = await repo.find(db, cred.id, payload.recipient_dept_id)
        if existing is not None:
            raise ConflictError(
                error_code="DEPT_GRANT_DUPLICATE",
                message="DeptGrant for this recipient already exists",
            )

        try:
            grant = await repo.create(
                db,
                id=_new_grant_id(),
                cred_id=cred.id,
                recipient_dept_id=payload.recipient_dept_id,
                granted_by_user_id=identity.user_id,
            )
            await db.commit()
            await db.refresh(grant)
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                error_code="DEPT_GRANT_DUPLICATE",
                message="DeptGrant conflict (race)",
            ) from exc
    except Exception as exc:
        _emit_failure(
            "tokens.dept_grant_added",
            target_id=cred_id,
            target_type="credential",
            exc=exc,
            extra={"recipient_dept_id": payload.recipient_dept_id},
        )
        raise

    audit_service.emit(
        "tokens.dept_grant_added",
        target_id=grant.id,
        target_type="dept_grant",
        details={
            "cred_id": cred.id,
            "recipient_dept_id": grant.recipient_dept_id,
        },
    )
    return grant


async def list_for(
    db: AsyncSession, identity: Identity, cred_id: str
) -> list[DeptGrant]:
    """Список DeptGrant'ов кред'ы. Видно owner/recipient dep_admin'ам и admin secret_service владеющего dep'а."""
    cred = await load_for_action(db, identity, cred_id, "read")
    _ensure_cross_dep_scope(cred)
    return await repo.get_for_cred(db, cred.id)


async def revoke(
    db: AsyncSession, identity: Identity, cred_id: str, grant_id: str
) -> None:
    """Снять DeptGrant + каскадно RoleACL'и в recipient_dep.

    На failure-пути (denied/not-found/scope-mismatch/db-error) эмитим
    `tokens.dept_grant_revoked / failure` ДО raise — SOC обязан видеть
    провалившиеся попытки revoke'а (это управляющая операция cross-dep flow).
    """
    try:
        cred = await load_for_action(db, identity, cred_id, "grant_dept")
        _ensure_cross_dep_scope(cred)

        grants = await repo.get_for_cred(db, cred.id)
        target = next((g for g in grants if g.id == grant_id), None)
        if target is None:
            raise NotFoundError(
                error_code="DEPT_GRANT_NOT_FOUND",
                message="DeptGrant not found for this credential",
            )

        recipient = target.recipient_dept_id
        grant_id_snapshot = target.id

        # Каскадное удаление RoleACL'ей (cred_id, recipient_dept).
        cascade_count = await acl_repo.delete_for_cred_dept(db, cred.id, recipient)
        await repo.delete(db, target)
        await db.commit()
    except Exception as exc:
        _emit_failure(
            "tokens.dept_grant_revoked",
            target_id=grant_id,
            target_type="dept_grant",
            exc=exc,
            extra={"cred_id": cred_id},
        )
        raise

    # cascade_role_acls в details уже несёт число снесённых ACL'ей —
    # отдельное dept_revoke_cascade-событие было дублем того же действия.
    audit_service.emit(
        "tokens.dept_grant_revoked",
        target_id=grant_id_snapshot,
        target_type="dept_grant",
        details={
            "cred_id": cred.id,
            "recipient_dept_id": recipient,
            "cascade_role_acls": cascade_count,
        },
    )
