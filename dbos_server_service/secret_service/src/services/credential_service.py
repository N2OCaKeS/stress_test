"""Use cases для credentials — CRUD + reveal + transfer + recover.

Слой бизнес-логики:

* валидация инвариантов scope/owner;
* проверка прав через `access_service.check_access`;
* envelope-encrypt/decrypt секрета;
* эмит audit-событий согласно README §«Audit catalog».

Cross-dep visibility-miss отдаётся как 404 (информационный leak protection).
Заблокированные креды — 410 GONE с `blocked_reason` в details.
"""

from __future__ import annotations

import base64
import logging
import secrets as _secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SERVICE_NAME
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    GoneError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.models import Credential
from src.repositories import credentials as repo
from src.schemas.credentials import (
    AdminDeleteRequest,
    CredentialCreate,
    CredentialUpdate,
    TransferRequest,
)
from src.services import access_service, audit_service, reveal_throttle, secrets_service

logger = logging.getLogger(__name__)


# Окно, в течение которого блокированную креду можно восстановить (см. README).
_RECOVER_WINDOW_DAYS = 30


def _new_cred_id() -> str:
    return f"cred_{_secrets.token_hex(16)}"


def _to_envelope(plaintext: str, cred_id: str) -> str:
    """Зашифровать secret через secrets_service. AAD прибит к cred_id."""
    return secrets_service.encrypt(
        plaintext, aad=secrets_service.aad_for_credential(cred_id)
    )


def _decrypt_envelope(token: str, cred_id: str) -> str:
    return secrets_service.decrypt(
        token, aad=secrets_service.aad_for_credential(cred_id)
    )


def _ensure_active_or_raise(cred: Credential) -> None:
    """Заблокированная — 410 GONE с blocked_reason в details."""
    if cred.status == "blocked":
        raise GoneError(
            error_code="CREDENTIAL_BLOCKED",
            message="Credential is blocked; contact platform admin",
            details={
                "blocked_reason": cred.blocked_reason,
                "blocked_at": cred.blocked_at.isoformat() if cred.blocked_at else None,
            },
        )


def _is_service_admin(identity: Identity) -> bool:
    return "admin" in identity.roles_for(SERVICE_NAME)


def _is_account_admin(identity: Identity) -> bool:
    return identity.platform_role == "account_admin"


async def create(
    db: AsyncSession, identity: Identity, payload: CredentialCreate
) -> Credential:
    """Создать креду. Personal → owner = identity, остальные → owner_dept_id из body."""
    # Resolve owner per scope.
    owner_user_id: str | None = None
    owner_dept_id: str | None = None
    if payload.scope == "personal":
        if identity.actor_type != "user":
            raise AuthorizationError(
                error_code="CREDENTIAL_ACCESS_DENIED",
                message="Bots cannot own personal credentials",
            )
        owner_user_id = identity.user_id
    else:
        owner_dept_id = payload.owner_dept_id
        # Только dep_admin собственного dep'а или service_admin может заводить
        # креды на чужой dep. Для своего dep'а — обычный operator+, тут не
        # фильтруем (auth+role-каталог уже отсеяли guest'ов на endpoint-level).
        if identity.department_id != owner_dept_id:
            if not (_is_service_admin(identity) or _is_account_admin(identity)):
                raise AuthorizationError(
                    error_code="CREDENTIAL_ACCESS_DENIED",
                    message="Cannot create credential for another department",
                )

    # Friendly UNIQUE pre-check (БД-CHECK всё равно ловит race).
    existing = await repo.find_active_by_owner_service_name(
        db,
        owner_user_id=owner_user_id,
        owner_dept_id=owner_dept_id,
        service=payload.service,
        name=payload.name,
    )
    if existing is not None:
        raise ConflictError(
            error_code="NAME_DUPLICATE",
            message=f"Credential with name={payload.name!r} already exists for this owner/service",
        )

    cred_id = _new_cred_id()
    envelope = _to_envelope(payload.secret, cred_id)
    try:
        cred = await repo.create(
            db,
            id=cred_id,
            name=payload.name,
            service=payload.service,
            scope=payload.scope,
            owner_user_id=owner_user_id,
            owner_dept_id=owner_dept_id,
            login=payload.login,
            secret_encrypted=envelope,
            status="active",
            created_by=identity.user_id,
        )
        await db.commit()
        await db.refresh(cred)
    except IntegrityError as exc:
        await db.rollback()
        # Race с другим INSERT — UNIQUE constraint поймал.
        raise ConflictError(
            error_code="NAME_DUPLICATE",
            message="Credential name conflict (race)",
        ) from exc

    audit_service.emit(
        "tokens.create",
        target_id=cred.id,
        target_type="credential",
        details={"scope": cred.scope, "service": cred.service, "name": cred.name},
    )
    return cred


async def list_visible(
    db: AsyncSession,
    identity: Identity,
    *,
    scope: str | None,
    service: str | None,
    status: str | None,
    limit: int,
    cursor: tuple[datetime, str] | None,
) -> tuple[list[Credential], str | None]:
    """Список видимых для identity кред с курсорной пагинацией.

    Простая стратегия: подтягиваем personal-креды actor'а (если он user) и
    department-креды его dep'а; через `check_access` фильтруем те, на которые
    у него фактический read-доступ. cross_dep recipient'ы видятся именно через
    репо `list_for_dept(identity.department_id)` — нам нужны также cred'ы,
    где actor — recipient, не owner. Для recipient'ов делаем отдельный
    запрос на pairs из dept_grants.

    Это не самая эффективная стратегия, но safe и читаема. Под нагрузкой
    можно перейти на JOIN через DeptGrant в репо.
    """
    visible: list[Credential] = []

    # Personal-креды и dept-креды одного запроса нет — сделаем по отдельности и
    # потом смерджим, отфильтруем по check_access. Этого хватит для phase 6.
    if identity.actor_type == "user":
        personal = await repo.list_for_user(
            db,
            identity.user_id,
            scope=scope,
            status=status,
            limit=limit * 2,
            cursor=cursor,
        )
        for cred in personal:
            allowed, _reason = await access_service.check_access(
                db, identity, cred, "read"
            )
            if allowed:
                visible.append(cred)

    if identity.department_id:
        dept = await repo.list_for_dept(
            db,
            identity.department_id,
            scope=scope,
            status=status,
            limit=limit * 2,
            cursor=cursor,
        )
        # dedupe по id (теоретически personal не пересекается с dept, но на
        # всякий — безопаснее).
        seen = {c.id for c in visible}
        for cred in dept:
            if cred.id in seen:
                continue
            allowed, _reason = await access_service.check_access(
                db, identity, cred, "read"
            )
            if allowed:
                visible.append(cred)

    # Сортировка по (created_at DESC, id DESC) и обрезка под limit.
    visible.sort(key=lambda c: (c.created_at, c.id), reverse=True)
    sliced = visible[:limit]
    next_cursor = None
    if len(visible) > limit:
        last = sliced[-1]
        next_cursor = f"{last.created_at.isoformat()}|{last.id}"
    return sliced, next_cursor


async def _would_have_read_access_if_active(
    db: AsyncSession, identity: Identity, cred: Credential
) -> bool:
    """Помогает решить blocked → 410 vs 404: были бы у actor read-права на
    active-версии этой cred'ы.

    check_access первым делом ловит status=="blocked", поэтому, чтобы спросить
    «а если бы было active», временно подменяем статус в in-memory объекте,
    делаем повторную проверку и возвращаем флаг. Объект не комитится.
    """
    saved_status = cred.status
    cred.status = "active"
    try:
        allowed, _reason = await access_service.check_access(db, identity, cred, "read")
        return allowed
    finally:
        cred.status = saved_status


# Reasons, которые означают «cred вне зоны видимости actor'а» — отдаём 404
# (info-leak protection). Остальные denied-reasons означают «cred в зоне
# видимости, но конкретного права нет» — отдаём 403.
_NOT_VISIBLE_REASONS: frozenset[str] = frozenset({
    "scope_mismatch",
    "dept_grant_missing",
})


async def load_for_action(
    db: AsyncSession, identity: Identity, cred_id: str, action: str
) -> Credential:
    """SELECT по PK + access-check. NotFound → 404, blocked → 410 для тех,
    кто имел бы read-доступ на active; для остальных — 404 (info-leak)."""
    cred = await repo.get_by_id(db, cred_id)
    if cred is None:
        raise NotFoundError(
            error_code="CREDENTIAL_NOT_FOUND",
            message="Credential not found",
        )

    allowed, reason = await access_service.check_access(db, identity, cred, action)  # type: ignore[arg-type]
    if not allowed:
        if reason == "blocked":
            # Решаем 410 vs 404: 410 видят те, кто имел бы read-доступ на
            # active; остальные — 404 (не светим существование).
            if await _would_have_read_access_if_active(db, identity, cred):
                _ensure_active_or_raise(cred)
            raise NotFoundError(
                error_code="CREDENTIAL_NOT_FOUND",
                message="Credential not found",
            )

        # Personal scope: любой denied — info-leak, отдаём 404.
        # Department/cross_department scope: 404 только при visibility miss.
        is_info_leak = (
            cred.scope == "personal"
            or reason in _NOT_VISIBLE_REASONS
        )
        if is_info_leak:
            raise NotFoundError(
                error_code="CREDENTIAL_NOT_FOUND",
                message="Credential not found",
            )

        # Audit denied — только для реальных 403 (actor видит cred, но не имеет права).
        audit_service.emit(
            "tokens.access_denied",
            target_id=cred.id,
            target_type="credential",
            status="failure",
            allowed=False,
            details={"action": action, "reason": reason},
        )
        raise AuthorizationError(
            error_code="CREDENTIAL_ACCESS_DENIED",
            message=f"Access denied for action={action!r}",
            details={"reason": reason},
        )

    # Доступ есть; если cred blocked И action != read — отдаём 410 явно.
    if cred.status == "blocked" and action != "read" and action != "manage_status":
        _ensure_active_or_raise(cred)
    return cred


async def get(db: AsyncSession, identity: Identity, cred_id: str) -> Credential:
    """GET карточки. blocked → 410 GONE если actor имел бы read-доступ к active."""
    return await load_for_action(db, identity, cred_id, "read")


async def update(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: CredentialUpdate,
) -> Credential:
    """PATCH name/login/secret. Secret меняется → re-encrypt."""
    cred = await load_for_action(db, identity, cred_id, "write")

    fields: dict = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.login is not None:
        fields["login"] = payload.login
    if payload.secret is not None:
        fields["secret_encrypted"] = _to_envelope(payload.secret, cred.id)

    if not fields:
        return cred

    try:
        cred = await repo.update(db, cred, **fields)
        await db.commit()
        await db.refresh(cred)
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            error_code="NAME_DUPLICATE",
            message="Credential name conflict",
        ) from exc

    audit_service.emit(
        "tokens.update",
        target_id=cred.id,
        target_type="credential",
        details={"fields": sorted(fields.keys())},
    )
    return cred


async def delete(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: AdminDeleteRequest | None,
) -> None:
    """DELETE кред. Admin override (service_admin/account_admin не-owner) требует reason."""
    cred = await repo.get_by_id(db, cred_id)
    if cred is None:
        raise NotFoundError(
            error_code="CREDENTIAL_NOT_FOUND",
            message="Credential not found",
        )

    # Проверка: владелец / dep_admin owner_dep / service_admin / account_admin.
    is_owner = (
        cred.scope == "personal"
        and identity.actor_type == "user"
        and cred.owner_user_id == identity.user_id
    )
    is_admin_override = False
    if not is_owner:
        # Попробуем разрешить как dep_admin owner_dep / service_admin.
        allowed, reason = await access_service.check_access(
            db, identity, cred, "delete"
        )
        if allowed:
            pass  # legitimate owner-side delete (dep_admin/service_admin внутри scope)
        elif _is_service_admin(identity) or _is_account_admin(identity):
            is_admin_override = True
        else:
            audit_service.emit(
                "tokens.access_denied",
                target_id=cred.id,
                target_type="credential",
                status="failure",
                allowed=False,
                details={"action": "delete", "reason": reason},
            )
            raise AuthorizationError(
                error_code="CREDENTIAL_ACCESS_DENIED",
                message="Access denied for delete",
                details={"reason": reason},
            )

    if is_admin_override and (payload is None or not payload.reason):
        raise DomainValidationError(
            error_code="ADMIN_OVERRIDE_REASON_REQUIRED",
            message="Admin override delete requires `reason` in body",
        )

    cred_id_snapshot = cred.id
    cred_scope = cred.scope
    cred_service = cred.service
    cred_name = cred.name

    await repo.delete(db, cred)
    await db.commit()

    if is_admin_override:
        audit_service.emit(
            "tokens.admin_override_delete",
            target_id=cred_id_snapshot,
            target_type="credential",
            details={
                "scope": cred_scope,
                "service": cred_service,
                "name": cred_name,
                "reason": payload.reason if payload else None,
            },
        )
    else:
        audit_service.emit(
            "tokens.delete",
            target_id=cred_id_snapshot,
            target_type="credential",
            details={"scope": cred_scope, "service": cred_service, "name": cred_name},
        )


async def reveal(
    db: AsyncSession, identity: Identity, cred_id: str
) -> tuple[str | None, str]:
    """Reveal plaintext. Возвращает `(login, secret_b64)`.

    Throttle: первый reveal в окне → CRITICAL `tokens.revealed`; последующие
    → INFO `tokens.revealed_throttled` с `count` в details.
    """
    cred = await load_for_action(db, identity, cred_id, "reveal")
    plaintext = _decrypt_envelope(cred.secret_encrypted, cred.id)
    secret_b64 = base64.b64encode(plaintext.encode("utf-8")).decode("ascii")

    is_first, count = await reveal_throttle.record_reveal(identity.user_id, cred.id)
    if is_first:
        audit_service.emit(
            "tokens.revealed",
            target_id=cred.id,
            target_type="credential",
            details={"count": count, "scope": cred.scope, "service": cred.service},
        )
    else:
        audit_service.emit(
            "tokens.revealed_throttled",
            target_id=cred.id,
            target_type="credential",
            details={"count": count, "scope": cred.scope, "service": cred.service},
        )

    return cred.login, secret_b64


async def transfer(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: TransferRequest,
) -> Credential:
    """Transfer ownership. Только для blocked-кред, service_admin/account_admin."""
    if not (_is_service_admin(identity) or _is_account_admin(identity)):
        raise AuthorizationError(
            error_code="CREDENTIAL_ACCESS_DENIED",
            message="Only service_admin or account_admin can transfer ownership",
        )
    cred = await repo.get_by_id(db, cred_id)
    if cred is None:
        raise NotFoundError(
            error_code="CREDENTIAL_NOT_FOUND",
            message="Credential not found",
        )

    if cred.status != "blocked":
        raise DomainValidationError(
            error_code="CREDENTIAL_NOT_BLOCKED",
            message="Transfer is only allowed for blocked credentials",
        )

    # Совместимость нового владельца со scope:
    if cred.scope == "personal":
        if not payload.new_owner_user_id:
            raise DomainValidationError(
                error_code="INVALID_TRANSFER_TARGET",
                message="personal cred requires new_owner_user_id",
            )
        cred.owner_user_id = payload.new_owner_user_id
        cred.owner_dept_id = None
    else:
        if not payload.new_owner_dept_id:
            raise DomainValidationError(
                error_code="INVALID_TRANSFER_TARGET",
                message=f"{cred.scope} cred requires new_owner_dept_id",
            )
        cred.owner_dept_id = payload.new_owner_dept_id
        cred.owner_user_id = None

    # Снимаем блокировку и комитим.
    cred.status = "active"
    cred.blocked_at = None
    cred.blocked_reason = None
    await db.flush()
    await db.commit()
    await db.refresh(cred)

    audit_service.emit(
        "tokens.transfer_ownership",
        target_id=cred.id,
        target_type="credential",
        details={
            "scope": cred.scope,
            "new_owner_user_id": cred.owner_user_id,
            "new_owner_dept_id": cred.owner_dept_id,
        },
    )
    return cred


async def recover(
    db: AsyncSession, identity: Identity, cred_id: str
) -> Credential:
    """Recover blocked-кред. Окно — 30 дней с момента блокировки."""
    if not (_is_service_admin(identity) or _is_account_admin(identity)):
        raise AuthorizationError(
            error_code="CREDENTIAL_ACCESS_DENIED",
            message="Only service_admin or account_admin can recover credential",
        )
    cred = await repo.get_by_id(db, cred_id)
    if cred is None:
        raise NotFoundError(
            error_code="CREDENTIAL_NOT_FOUND",
            message="Credential not found",
        )
    if cred.status != "blocked":
        raise DomainValidationError(
            error_code="CREDENTIAL_NOT_BLOCKED",
            message="Only blocked credentials can be recovered",
        )
    if cred.blocked_at is not None:
        # Если blocked_at без TZ — относимся как к UTC (db: timestamptz).
        blocked_at = cred.blocked_at
        if blocked_at.tzinfo is None:
            blocked_at = blocked_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - blocked_at > timedelta(days=_RECOVER_WINDOW_DAYS):
            raise DomainValidationError(
                error_code="RECOVER_WINDOW_EXPIRED",
                message=f"Recover window of {_RECOVER_WINDOW_DAYS} days has expired",
            )

    cred = await repo.mark_active(db, cred)
    await db.commit()
    await db.refresh(cred)

    audit_service.emit(
        "tokens.recover",
        target_id=cred.id,
        target_type="credential",
        details={"scope": cred.scope, "service": cred.service},
    )
    return cred
