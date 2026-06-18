"""Use cases для управления per-account ACL (прямые гранты на учётку).

Аддитивно к ролевой матрице: грант раздаёт конкретному пользователю набор
действий на одну учётку. Управление гейтится `(server_account,
manage_account_acl)`; видимость учётки — dept-isolation (cross-dept за 404).

Грант хранит `department_id` учётки и живёт в её отделе. user_id записывается
как soft-FK (`usr_`/`bot_`); проверка, что пользователь существует и
принадлежит этому отделу, на стороне server_service невозможна (users живут в
auth_service) — мы фиксируем отдел учётки и доверяем dept-bound грантору.
Cross-dept утечка закрыта на уровне видимости учётки: грантовать можно только
учётку своего отдела.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    ACCOUNT_ACL_ACTION_COLUMNS,
    ACCOUNT_ACL_ACTIONS,
    Action,
    EntityType,
)
from src.core.exceptions import DomainValidationError, NotFoundError
from src.models import ServerAccount, ServerAccountUserAcl
from src.repositories import server_account as account_repo
from src.repositories import server_account_user_acl as acl_repo
from src.schemas.identity import IdentityContext
from src.schemas.server_account import AccountAclGrantRequest
from src.services import audit_service, permissions
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import account_acl_id

logger = logging.getLogger(__name__)

# Канонический порядок действий в ответе (совпадает с порядком объявления
# колонок в маппинге). UI опирается на этот порядок.
_ACTION_ORDER: list[str] = list(ACCOUNT_ACL_ACTION_COLUMNS.keys())


def acl_to_actions(grant: ServerAccountUserAcl) -> list[str]:
    """Развернуть boolean-флаги гранта в список разрешённых действий (канон. порядок)."""
    return [
        action
        for action in _ACTION_ORDER
        if getattr(grant, ACCOUNT_ACL_ACTION_COLUMNS[action], False)
    ]


async def _load_account_visible(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    audit_action: str,
) -> ServerAccount:
    """SELECT учётки + dept-isolation. 404 на невидимую (cross-dept/нет row)."""
    account = await account_repo.get_by_id(db, account_id)
    if account is None or account.department_id != identity.department_id:
        audit_service.emit(
            audit_action, target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND", message="Server account not found"
        )
    return account


async def list_acl(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> tuple[ServerAccount, list[ServerAccountUserAcl]]:
    """Список прямых грантов учётки. Право: `(server_account, manage_account_acl)`."""
    with emit_denied_on_authz_error(
        "server_account.acl_listed",
        target_id=account_id,
        target_type="server_account",
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.MANAGE_ACCOUNT_ACL
        )
    account = await _load_account_visible(
        db, identity, account_id, "server_account.acl_listed"
    )
    grants = await acl_repo.list_for_account(db, account_id)
    audit_service.emit(
        "server_account.acl_listed",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={"department_id": account.department_id, "count": len(grants)},
    )
    return account, grants


async def grant_acl(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    payload: AccountAclGrantRequest,
) -> ServerAccountUserAcl:
    """Выдать (или перевыдать) прямой грант пользователю на учётку.

    Право: `(server_account, manage_account_acl)`. Валидация: учётка видна
    (dept-isolation), все действия из каталога ACCOUNT_ACL_ACTIONS, user_id —
    валидный soft-FK. Повторная выдача той же паре заменяет набор флагов.
    Аудит WARNING.
    """
    with emit_denied_on_authz_error(
        "server_account.acl_granted",
        target_id=account_id,
        target_type="server_account",
        extra_details={"target_user_id": payload.user_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.MANAGE_ACCOUNT_ACL
        )
    account = await _load_account_visible(
        db, identity, account_id, "server_account.acl_granted"
    )

    requested = set(payload.actions)
    unknown = requested - set(ACCOUNT_ACL_ACTIONS)
    if unknown:
        audit_service.emit(
            "server_account.acl_granted",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "invalid_actions",
                "target_user_id": payload.user_id,
                "unknown_actions": sorted(unknown),
            },
        )
        raise DomainValidationError(
            error_code="INVALID_ACL_ACTION",
            message="One or more requested actions are not grantable per-account",
            details={"unknown_actions": sorted(unknown)},
        )

    # user_id soft-FK формат: usr_/bot_. Чужой/битый — 422. Полноценную
    # проверку принадлежности к отделу даёт auth_service (здесь его БД нет).
    if not (payload.user_id.startswith("usr_") or payload.user_id.startswith("bot_")):
        audit_service.emit(
            "server_account.acl_granted",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "invalid_user_id", "target_user_id": payload.user_id},
        )
        raise DomainValidationError(
            error_code="INVALID_USER_ID",
            message="user_id must be a usr_/bot_ soft-FK",
        )

    grant = await acl_repo.upsert(
        db,
        acl_id=account_acl_id(),
        account_id=account_id,
        user_id=payload.user_id,
        department_id=account.department_id,
        actions=requested,
        created_by=identity.user_id,
    )
    await db.commit()
    await db.refresh(grant)
    audit_service.emit(
        "server_account.acl_granted",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "target_user_id": payload.user_id,
            "actions": acl_to_actions(grant),
            "department_id": account.department_id,
        },
    )
    return grant


async def revoke_acl(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    user_id: str,
) -> None:
    """Снять прямой грант пользователя на учётку.

    Право: `(server_account, manage_account_acl)`. Если гранта нет — 404.
    Аудит WARNING.
    """
    with emit_denied_on_authz_error(
        "server_account.acl_revoked",
        target_id=account_id,
        target_type="server_account",
        extra_details={"target_user_id": user_id},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.MANAGE_ACCOUNT_ACL
        )
    account = await _load_account_visible(
        db, identity, account_id, "server_account.acl_revoked"
    )
    removed = await acl_repo.delete(db, account_id, user_id)
    if removed == 0:
        audit_service.emit(
            "server_account.acl_revoked",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "not_found",
                "target_user_id": user_id,
                "department_id": account.department_id,
            },
        )
        raise NotFoundError(
            error_code="ACL_GRANT_NOT_FOUND",
            message="No direct grant for this user on this account",
        )
    await db.commit()
    audit_service.emit(
        "server_account.acl_revoked",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={"target_user_id": user_id, "department_id": account.department_id},
    )
