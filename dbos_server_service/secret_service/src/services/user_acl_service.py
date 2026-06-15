"""Use cases для /credentials/{id}/user-acl — CredentialUserACL CRUD.

Per-user слой доступа: владелец personal-кред'ы (или dep_admin для
department/cross_department) выдаёт доступ поимённо конкретному user_id.
Управление гейтится тем же action'ом `grant_acl`, что и RoleACL — кто может
раздавать роли, тот может раздавать и поимённый доступ.

Выдать доступ самому себе нельзя: для personal владелец и так допущен по
owner_match, для прочих scope'ов это бессмысленный self-grant → 422.
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
from src.models import CredentialUserACL
from src.repositories import user_acls as repo
from src.schemas.user_acls import UserACLCreate
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
    return f"uacl_{_secrets.token_hex(16)}"


async def add(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: UserACLCreate,
) -> CredentialUserACL:
    """Создать user-ACL. Требует grant_acl на креде.

    На failure-пути эмитим `tokens.user_acl_added / failure` до raise.
    """
    try:
        cred = await load_for_action(db, identity, cred_id, "grant_acl")

        # Self-grant: для personal владелец уже допущен по owner_match, для
        # прочих scope'ов выдавать себе поимённый доступ бессмысленно.
        target_user = payload.user_id
        if cred.scope == "personal" and target_user == cred.owner_user_id:
            raise DomainValidationError(
                error_code="USER_ACL_OWNER_REDUNDANT",
                message="cannot grant user-ACL to the credential owner",
                details={"user_id": target_user},
            )
        if target_user == identity.user_id:
            raise DomainValidationError(
                error_code="USER_ACL_SELF_REDUNDANT",
                message="cannot grant user-ACL to yourself",
                details={"user_id": target_user},
            )

        existing = await repo.find(db, cred.id, target_user)
        if existing is not None:
            raise ConflictError(
                error_code="USER_ACL_DUPLICATE",
                message="user-ACL for this user already exists",
            )

        try:
            acl = await repo.create(
                db,
                id=_new_acl_id(),
                cred_id=cred.id,
                user_id=target_user,
                can_read=payload.can_read,
                can_write=payload.can_write,
                granted_by_user_id=identity.user_id,
            )
            await db.commit()
            await db.refresh(acl)
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                error_code="USER_ACL_DUPLICATE",
                message="user-ACL conflict (race)",
            ) from exc
    except Exception as exc:
        _emit_failure(
            "tokens.user_acl_added",
            target_id=cred_id,
            target_type="credential",
            exc=exc,
            extra={"user_id": payload.user_id},
        )
        raise

    audit_service.emit(
        "tokens.user_acl_added",
        target_id=acl.id,
        target_type="user_acl",
        details={
            "cred_id": cred.id,
            "user_id": acl.user_id,
            "can_read": acl.can_read,
            "can_write": acl.can_write,
        },
    )
    return acl


async def list_for(
    db: AsyncSession, identity: Identity, cred_id: str
) -> list[CredentialUserACL]:
    """Список user-ACL'ей кред'ы. Доступ — как у read."""
    cred = await load_for_action(db, identity, cred_id, "read")
    return await repo.get_for_cred(db, cred.id)


async def revoke(
    db: AsyncSession, identity: Identity, cred_id: str, acl_id: str
) -> None:
    """Снять user-ACL. Требует grant_acl.

    На failure-пути эмитим `tokens.user_acl_removed / failure` до raise.
    """
    try:
        cred = await load_for_action(db, identity, cred_id, "grant_acl")

        acls = await repo.get_for_cred(db, cred.id)
        target = next((a for a in acls if a.id == acl_id), None)
        if target is None:
            raise NotFoundError(
                error_code="USER_ACL_NOT_FOUND",
                message="user-ACL not found for this credential",
            )

        target_id = target.id
        target_user = target.user_id

        await repo.delete(db, target)
        await db.commit()
    except Exception as exc:
        _emit_failure(
            "tokens.user_acl_removed",
            target_id=acl_id,
            target_type="user_acl",
            exc=exc,
            extra={"cred_id": cred_id},
        )
        raise

    audit_service.emit(
        "tokens.user_acl_removed",
        target_id=target_id,
        target_type="user_acl",
        details={"cred_id": cred.id, "user_id": target_user},
    )
