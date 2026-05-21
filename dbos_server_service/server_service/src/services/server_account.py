"""Use cases для server_accounts — CRUD + rotate_password + reveal_password.

`view_password` остаётся в `internal_service` (worker-only). `reveal_password`
— user-facing endpoint, отдающий plaintext в base64 (UI/CLI хранят пароли в
менеджере секретов или показывают пользователю). Default — только
admin/operator.

Visibility-check (cross-department) скрывает чужие аккаунты за 404, чтобы
не выдавать факт существования. Симметрично с `services/server.py`.
"""

import base64
import logging
import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from src.models import Server, ServerAccount
from src.repositories import server as server_repo
from src.repositories import server_account as repo
from src.schemas.identity import IdentityContext
from src.schemas.server_account import ServerAccountCreate, ServerAccountUpdate
from src.services import audit_service, permissions, secrets_service
from src.services.audit_helpers import emit_denied_on_authz_error
from src.services.server import load_visible_server
from src.utils.ids import server_account_id as new_id

logger = logging.getLogger(__name__)


async def _load_account_visible(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> tuple[ServerAccount, Server]:
    """SELECT аккаунта + его сервера + dept-isolation. 404 во всех ambiguity-ветках."""
    account = await repo.get_by_id(db, account_id)
    if account is None:
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    server = await server_repo.get_by_id(db, account.server_id)
    if server is None:
        # Не должно случаться (FK CASCADE), но обрабатываем как 404 на всякий случай.
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    if identity.department_id != server.department_id:
        # Скрываем существование аккаунта чужого dept за тем же 404, что и
        # для несуществующего id — иначе по разнице ответов утечёт enumeration.
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    return account, server


def _generate_password() -> str:
    """Дефолтный генератор паролей для новых аккаунтов и rotate'а."""
    return secrets.token_urlsafe(32)


async def create_account(
    db: AsyncSession,
    identity: IdentityContext,
    payload: ServerAccountCreate,
) -> ServerAccount:
    """INSERT нового аккаунта.

    Порядок проверок:
      1. CREATE permission.
      2. Server существует + dept caller'а совпадает (иначе 404 — скрываем
         факт существования чужого сервера).
      3. has_sudo=True → дополнительно требует GRANT_SUDO action.
      4. Шифруем password (переданный или сгенерированный).
      5. INSERT + commit. UNIQUE(server_id, login) → 409 ACCOUNT_DUPLICATE.
    """
    with emit_denied_on_authz_error(
        "server_account.create",
        target_type="server_account",
        extra_details={"server_id": payload.server_id},
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.CREATE
        )

    try:
        server = await load_visible_server(db, identity, payload.server_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.create",
            target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": payload.server_id},
        )
        raise

    if payload.has_sudo:
        try:
            await permissions.require_action(
                db, identity, EntityType.SERVER_ACCOUNT, Action.GRANT_SUDO
            )
        except AuthorizationError:
            audit_service.emit(
                "server_account.create",
                target_type="server_account",
                status="denied", allowed=False,
                details={
                    "reason": "grant_sudo_denied",
                    "server_id": payload.server_id,
                    "login": payload.login,
                },
            )
            raise

    plaintext = payload.password if payload.password is not None else _generate_password()
    account_id = new_id()
    encrypted = secrets_service.encrypt(
        plaintext,
        aad=secrets_service.aad_for_server_account_password(account_id),
    )
    data = {
        "id": account_id,
        "server_id": payload.server_id,
        "login": payload.login,
        "password_encrypted": encrypted,
        "has_sudo": payload.has_sudo,
        "unix_groups": list(payload.unix_groups),
        "linked_user_id": payload.linked_user_id,
        "shell": payload.shell,
        "home_dir": payload.home_dir,
        "is_active": True,
        "created_by": identity.user_id,
    }
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании аккаунта: %s", exc.orig)
        audit_service.emit(
            "server_account.create",
            target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "duplicate",
                "server_id": payload.server_id,
                "login": payload.login,
            },
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="Account with this login already exists on this server",
            details={"hint": "уникальный ключ (server_id, login)"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_account.create",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": obj.server_id,
            "login": obj.login,
            "has_sudo": obj.has_sudo,
            "department_id": server.department_id,
        },
    )
    return obj


async def get_account(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> ServerAccount:
    """SELECT по PK + dept-isolation."""
    with emit_denied_on_authz_error(
        "server_account.view",
        target_id=account_id,
        target_type="server_account",
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW
        )
    try:
        account, _server = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.view",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    return account


async def list_accounts(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    limit: int,
    offset: int,
) -> tuple[list[ServerAccount], int]:
    """List + count для одного сервера. Cross-dept сервер скрыт за 404."""
    with emit_denied_on_authz_error(
        "server_account.list",
        target_type="server_account",
        extra_details={"server_id": server_id},
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW
        )
    try:
        await load_visible_server(db, identity, server_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.list",
            target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "server_not_found_or_cross_dept", "server_id": server_id},
        )
        raise
    items = await repo.list_for_server(db, server_id, limit=limit, offset=offset)
    total = await repo.count_for_server(db, server_id)
    return items, total


async def update_account(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    payload: ServerAccountUpdate,
) -> ServerAccount:
    """PATCH-обновление. Пустой диф → возврат без UPDATE.

    `has_sudo=True` дополнительно требует GRANT_SUDO. Изменение пароля
    через PATCH не предусмотрено — только через `/rotate_password`.
    """
    with emit_denied_on_authz_error(
        "server_account.update",
        target_id=account_id,
        target_type="server_account",
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.UPDATE
        )
    try:
        obj, server = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.update",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj

    # has_sudo=True или подъём флага — требует GRANT_SUDO. Снятие флага
    # допустимо обычным UPDATE — это понижение привилегии.
    if changes.get("has_sudo") is True and not obj.has_sudo:
        try:
            await permissions.require_action(
                db, identity, EntityType.SERVER_ACCOUNT, Action.GRANT_SUDO
            )
        except AuthorizationError:
            audit_service.emit(
                "server_account.update",
                target_id=account_id, target_type="server_account",
                status="denied", allowed=False,
                details={
                    "reason": "grant_sudo_denied",
                    "fields": list(changes.keys()),
                },
            )
            raise

    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на обновлении аккаунта %s: %s", account_id, exc.orig)
        audit_service.emit(
            "server_account.update",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="Update collides with an existing account",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_account.update",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "fields": list(changes.keys()),
            "server_id": obj.server_id,
            "department_id": server.department_id,
        },
    )
    return obj


async def delete_account(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> None:
    """Hard-delete аккаунта."""
    with emit_denied_on_authz_error(
        "server_account.delete",
        target_id=account_id,
        target_type="server_account",
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.DELETE
        )
    try:
        obj, server = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.delete",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    server_id = obj.server_id
    login = obj.login
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "server_account.delete",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "login": login,
            "department_id": server.department_id,
        },
    )


async def rotate_password(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> ServerAccount:
    """User-инициированная ротация пароля. Новый пароль НЕ возвращается клиенту.

    Worker-callback path (`internal.rotate_account_password`) принимает уже
    готовый password от worker'а (после SSH-apply). Этот же путь — для
    случая, когда пароль надо сгенерить и сохранить локально (например,
    реакция на компрометацию, без apply'я на хост). Различимы по
    `details.reason` в audit-event'е.
    """
    with emit_denied_on_authz_error(
        "server_account.rotate_password",
        target_id=account_id,
        target_type="server_account",
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.ROTATE_PASSWORD
        )
    try:
        obj, server = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.rotate_password",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    new_password = _generate_password()
    encrypted = secrets_service.encrypt(
        new_password,
        aad=secrets_service.aad_for_server_account_password(obj.id),
    )
    updated = await repo.update_password(db, obj, encrypted)
    await db.commit()
    await db.refresh(updated)
    audit_service.emit(
        "server_account.rotate_password",
        target_id=updated.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": updated.server_id,
            "login": updated.login,
            "department_id": server.department_id,
            "reason": "user_initiated",
            "rotated_at": updated.password_rotated_at.isoformat() if updated.password_rotated_at else None,
        },
    )
    return updated


async def reveal_password(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> str:
    """Расшифровать пароль аккаунта и вернуть его base64-encoded.

    Порядок проверок:
      1. Visibility (cross-dept — 404, скрываем существование).
      2. Permission `reveal_password` — по дефолту только admin/operator
         (отдельно от worker-only `view_password`).
      3. `secrets_service.decrypt` с правильным aad. При сломанном
         ciphertext поднимается `AppException(DECRYPT_FAILED, http_status=500)`
         — пробрасываем как есть, дополнительно эмитим failure-audit.
      4. На успех — audit `server_account.password_revealed`
         (severity WARNING — чувствительная операция).
    """
    try:
        account, server = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.password_revealed",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise

    with emit_denied_on_authz_error(
        "server_account.password_revealed",
        target_id=account_id,
        target_type="server_account",
        extra_details={
            "server_id": account.server_id,
            "department_id": server.department_id,
        },
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.REVEAL_PASSWORD
        )

    if account.password_encrypted is None:
        audit_service.emit(
            "server_account.password_revealed",
            target_id=account.id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "no_password_stored",
                "server_id": account.server_id,
                "department_id": server.department_id,
            },
        )
        raise NotFoundError(
            error_code="ACCOUNT_HAS_NO_PASSWORD",
            message="Account has no stored password",
        )

    try:
        plain = secrets_service.decrypt(
            account.password_encrypted,
            aad=secrets_service.aad_for_server_account_password(account.id),
        )
    except Exception:
        audit_service.emit(
            "server_account.password_revealed",
            target_id=account.id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "decrypt_failed",
                "server_id": account.server_id,
                "department_id": server.department_id,
            },
        )
        raise

    audit_service.emit(
        "server_account.password_revealed",
        target_id=account.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": account.server_id,
            "login": account.login,
            "department_id": server.department_id,
        },
    )
    return base64.b64encode(plain.encode("utf-8")).decode("ascii")
