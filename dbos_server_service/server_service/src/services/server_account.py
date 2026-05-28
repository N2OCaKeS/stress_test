"""Use cases для server_accounts — CRUD + rotate_password + M2M-линковка.

Аккаунт привязан к набору серверов (many-to-many через
`server_account_servers`). Пароль — общий на все привязанные серверы и
хранится на строке аккаунта. Карточка аккаунта доступна по `view`. Если
вызывающий вдобавок держит `view_password`, тот же GET доносит расшифрованный
пароль в base64 — отдельной reveal-ручки нет. Internal endpoint
(`internal_service`) для worker'а остаётся.

Visibility-check (cross-department) скрывает чужие аккаунты за 404, чтобы
не выдавать факт существования. Аккаунт видим, если его `department_id`
совпадает с caller'ом. Симметрично с `services/server.py`.
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
from src.repositories import server_account as repo
from src.schemas.identity import IdentityContext
from src.schemas.server_account import (
    ServerAccountCreate,
    ServerAccountServersUpdate,
    ServerAccountUpdate,
)
from src.services import audit_service, permissions, secrets_service
from src.services.audit_helpers import emit_denied_on_authz_error
from src.services.server import load_visible_server
from src.utils.ids import server_account_id as new_id

logger = logging.getLogger(__name__)


async def _load_account_visible(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> ServerAccount:
    """SELECT аккаунта + dept-isolation. 404 во всех ambiguity-ветках.

    Видимость теперь строится на `account.department_id` (аккаунт может
    жить сразу на нескольких серверах одного отдела), а не на одиночном
    server'е.
    """
    account = await repo.get_by_id(db, account_id)
    if account is None:
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    if identity.department_id != account.department_id:
        # Скрываем существование аккаунта чужого dept за тем же 404, что и
        # для несуществующего id — иначе по разнице ответов утечёт enumeration.
        raise NotFoundError(error_code="ACCOUNT_NOT_FOUND", message="Server account not found")
    return account


def _generate_password() -> str:
    """Дефолтный генератор паролей для новых аккаунтов и rotate'а."""
    return secrets.token_urlsafe(32)


async def _resolve_same_dept_servers(
    db: AsyncSession,
    identity: IdentityContext,
    server_ids: list[str],
    audit_action: str,
) -> list[Server]:
    """Подгрузить все серверы из списка с dept-isolation.

    Любой server чужого/несуществующего dept'а → 404 (скрываем факт
    существования). На вход уже идёт дедуплицированный список.
    """
    servers: list[Server] = []
    for sid in server_ids:
        try:
            srv = await load_visible_server(db, identity, sid)
        except NotFoundError:
            audit_service.emit(
                audit_action,
                target_type="server_account",
                status="denied", allowed=False,
                details={"reason": "server_not_found_or_cross_dept", "server_id": sid},
            )
            raise
        servers.append(srv)
    return servers


async def create_account(
    db: AsyncSession,
    identity: IdentityContext,
    payload: ServerAccountCreate,
) -> ServerAccount:
    """INSERT нового аккаунта + привязка к списку серверов.

    Порядок проверок:
      1. CREATE permission.
      2. Каждый сервер из `server_ids` существует + dept caller'а совпадает
         (иначе 404 — скрываем факт существования чужого сервера).
      3. has_sudo=True → дополнительно требует GRANT_SUDO action.
      4. Шифруем password (переданный или сгенерированный).
      5. INSERT аккаунта + связок. UNIQUE(server_id, login) на join →
         409 ACCOUNT_DUPLICATE (login занят на одном из серверов).
    """
    with emit_denied_on_authz_error(
        "server_account.create",
        target_type="server_account",
        extra_details={"server_ids": payload.server_ids},
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.CREATE
        )

    servers = await _resolve_same_dept_servers(
        db, identity, payload.server_ids, "server_account.create"
    )
    # Все серверы из одного отдела (caller'а) — department аккаунта берём
    # из caller'а; load_visible_server уже гарантировал совпадение.
    department_id = identity.department_id

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
                    "server_ids": payload.server_ids,
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
        "department_id": department_id,
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
        obj = await repo.create(db, data, [s.id for s in servers])
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании аккаунта: %s", type(exc.orig).__name__)
        audit_service.emit(
            "server_account.create",
            target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "duplicate",
                "server_ids": payload.server_ids,
                "login": payload.login,
            },
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="Account with this login already exists on one of the servers",
            details={"hint": "уникальный ключ (server_id, login) на join-таблице"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_account.create",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_ids": [s.id for s in servers],
            "login": obj.login,
            "has_sudo": obj.has_sudo,
            "department_id": department_id,
        },
    )
    return obj


async def get_account(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> tuple[ServerAccount, str | None]:
    """SELECT по PK + dept-isolation, опционально с расшифрованным паролем.

    Карточка доступна по `view` или `view_password`: держателю `view_password`
    голый `view` не нужен (так worker_bot, у которого только secret-access,
    тоже читает карточку с паролем). Второй элемент кортежа — base64(plaintext)
    при наличии `view_password`, иначе `None`. Раскрытие пароля пишет отдельный
    CRITICAL-аудит `server_account.password_revealed`.
    """
    has_password_action = await permissions.has_action(
        db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW_PASSWORD
    )
    with emit_denied_on_authz_error(
        "server_account.view",
        target_id=account_id,
        target_type="server_account",
    ):
        if not has_password_action:
            await permissions.require_action(
                db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW
            )
    try:
        account = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.view",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise

    if not has_password_action:
        return account, None

    return account, _reveal_account_password(account)


async def list_accounts(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    limit: int,
    offset: int,
) -> tuple[list[ServerAccount], int]:
    """List + count привязанных к серверу аккаунтов. Cross-dept сервер скрыт за 404."""
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
    Привязка серверов — через `/servers` под-операции.
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
        obj = await _load_account_visible(db, identity, account_id)
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
        logger.warning("IntegrityError на обновлении аккаунта %s: %s", account_id, type(exc.orig).__name__)
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
            "department_id": obj.department_id,
        },
    )
    return obj


async def link_servers(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    payload: ServerAccountServersUpdate,
) -> ServerAccount:
    """Привязать аккаунт к дополнительным серверам.

    Линковка гейтится `update`. Все новые серверы обязаны быть в том же
    department'е, что и аккаунт (cross-dept → 404, как при create). Если
    login уже занят на одном из серверов другим аккаунтом — 409.
    """
    with emit_denied_on_authz_error(
        "server_account.link_servers",
        target_id=account_id,
        target_type="server_account",
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.UPDATE
        )
    try:
        obj = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.link_servers",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise

    await _resolve_same_dept_servers(
        db, identity, payload.server_ids, "server_account.link_servers"
    )

    try:
        await repo.add_servers(db, obj, payload.server_ids)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на линковке аккаунта %s: %s", account_id, type(exc.orig).__name__)
        audit_service.emit(
            "server_account.link_servers",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "duplicate", "server_ids": payload.server_ids},
        )
        raise ConflictError(
            error_code="ACCOUNT_DUPLICATE",
            message="Account login already exists on one of the target servers",
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "server_account.link_servers",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_ids": payload.server_ids,
            "department_id": obj.department_id,
        },
    )
    return obj


async def unlink_servers(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    payload: ServerAccountServersUpdate,
) -> ServerAccount:
    """Отвязать аккаунт от серверов.

    Отвязка гейтится `update`. Нельзя снять последнюю связку — аккаунт всегда
    живёт хотя бы на одном сервере (иначе → 409 ACCOUNT_NO_SERVERS).
    """
    with emit_denied_on_authz_error(
        "server_account.unlink_servers",
        target_id=account_id,
        target_type="server_account",
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.UPDATE
        )
    try:
        obj = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.unlink_servers",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise

    current = set(repo.linked_server_ids(obj))
    unknown = [sid for sid in payload.server_ids if sid not in current]
    if unknown:
        audit_service.emit(
            "server_account.unlink_servers",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "server_not_linked",
                "server_ids": payload.server_ids,
                "unknown_server_ids": unknown,
            },
        )
        raise NotFoundError(
            error_code="ACCOUNT_SERVER_LINK_NOT_FOUND",
            message="Account is not linked to one or more of the requested servers",
            details={"unknown_server_ids": unknown},
        )
    remaining = current - set(payload.server_ids)
    if not remaining:
        audit_service.emit(
            "server_account.unlink_servers",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "would_orphan_account", "server_ids": payload.server_ids},
        )
        raise ConflictError(
            error_code="ACCOUNT_NO_SERVERS",
            message="Cannot unlink the last server — account must stay on at least one",
        )

    removed = await repo.remove_servers(db, obj, payload.server_ids)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "server_account.unlink_servers",
        target_id=obj.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_ids": payload.server_ids,
            "removed": removed,
            "department_id": obj.department_id,
        },
    )
    return obj


async def delete_account(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
) -> None:
    """Hard-delete аккаунта (связки уходят каскадом)."""
    with emit_denied_on_authz_error(
        "server_account.delete",
        target_id=account_id,
        target_type="server_account",
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.DELETE
        )
    try:
        obj = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.delete",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    login = obj.login
    server_ids = repo.linked_server_ids(obj)
    department_id = obj.department_id
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "server_account.delete",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_ids": server_ids,
            "login": login,
            "department_id": department_id,
        },
    )


async def rotate_password(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    new_password: str | None = None,
) -> ServerAccount:
    """User-инициированная ротация общего пароля. Новый пароль НЕ возвращается клиенту.

    Пароль общий на все привязанные серверы — эта ручка меняет ciphertext в
    БД без SSH-apply'я. Для apply'я на конкретный сервер или на все — см.
    worker-dispatch (`/rotate` точечный/массовый).

    Если `new_password` передан — он уже прошёл парольную политику на схеме
    (`ServerAccountRotateRequest`) и сохраняется как есть. Если нет —
    генерируем серверной стороной (`secrets.token_urlsafe(32)`).
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
        obj = await _load_account_visible(db, identity, account_id)
    except NotFoundError:
        audit_service.emit(
            "server_account.rotate_password",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise
    plaintext = new_password if new_password is not None else _generate_password()
    encrypted = secrets_service.encrypt(
        plaintext,
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
            "login": updated.login,
            "department_id": updated.department_id,
            "reason": "user_provided" if new_password is not None else "user_initiated",
            "rotated_at": updated.password_rotated_at.isoformat() if updated.password_rotated_at else None,
        },
    )
    return updated


def _reveal_account_password(account: ServerAccount) -> str | None:
    """Расшифровать пароль аккаунта в base64 и записать аудит раскрытия.

    Вызывается из `get_account` только после успешной проверки `view_password`,
    поэтому permission тут уже не проверяется. Возвращает `None`, если у
    аккаунта нет сохранённого пароля (карточка всё равно отдаётся без пароля).
    Раскрытие пишет CRITICAL-аудит `server_account.password_revealed`;
    сломанный ciphertext поднимает `DECRYPT_FAILED` (500) + failure-аудит.
    """
    if account.password_encrypted is None:
        audit_service.emit(
            "server_account.password_revealed",
            target_id=account.id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "no_password_stored",
                "department_id": account.department_id,
            },
        )
        return None

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
                "department_id": account.department_id,
            },
        )
        raise

    audit_service.emit(
        "server_account.password_revealed",
        target_id=account.id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": account.login,
            "department_id": account.department_id,
        },
    )
    return base64.b64encode(plain.encode("utf-8")).decode("ascii")
