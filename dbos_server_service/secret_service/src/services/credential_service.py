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

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SERVICE_NAME
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    GoneError,
    NotFoundError,
    RateLimitError,
)
from src.dependencies.auth import Identity
from src.models import Credential, DeptGrant, RoleACL
from src.repositories import credentials as repo
from src.schemas.credentials import (
    AdminDeleteRequest,
    CredentialCreate,
    CredentialUpdate,
    TransferRequest,
)
from src.services import (
    access_service,
    audit_service,
    lockout_service,
    reveal_throttle,
    secrets_service,
)

logger = logging.getLogger(__name__)


# Окно, в течение которого блокированную креду можно восстановить (см. README).
_RECOVER_WINDOW_DAYS = 30


def _emit_action_failure(
    action: str,
    *,
    target_id: str | None,
    target_type: str,
    exc: Exception,
    extra: dict | None = None,
) -> None:
    """Emit failure-вариант action'а в audit. Без re-raise: caller сам решает.

    Compliance: SOC должен видеть deny/error для CRUD/transfer/recover/grant —
    раньше эмит был только на success-ветке, и raise происходил ДО emit'а.
    error_code конкретизирует причину (NAME_DUPLICATE, NOT_BLOCKED и т.п.),
    error_class — тип исключения для разбора неклассифицированных ошибок.
    """
    details: dict = {
        "error_class": type(exc).__name__,
    }
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
        # audit-канал не должен сломать основной поток.
        logger.warning(
            "audit_emit_failed action=%s err=%s",
            action,
            type(emit_exc).__name__,
        )


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


def _decrypt_envelope_with_meta(
    token: str, cred_id: str
) -> secrets_service.DecryptResult:
    return secrets_service.decrypt_with_metadata(
        token, aad=secrets_service.aad_for_credential(cred_id)
    )


async def _lazy_reencrypt_if_needed(
    db: AsyncSession,
    cred: Credential,
    result: secrets_service.DecryptResult,
) -> None:
    """Перешифровать ciphertext активной версией ключа и закоммитить.

    Вызывается из read-path'а (`reveal`) когда `result.needs_reencrypt` поднят.
    Стратегия — idempotent CAS: новый blob ставится только если в БД ровно тот
    blob, который мы только что прочитали. Конкурентный reveal/PATCH той же
    строки увидит 0 rows affected — это норма, в чате остаётся одна актуальная
    версия. Любая ошибка ловится и логируется WARNING — read-path должен
    вернуться успешно даже при временной недоступности БД на запись.

    Важно: CAS-UPDATE без commit'а раскатывается обратно на закрытии сессии
    через `get_db` (rollback-on-close), поэтому строка никогда не мигрировала
    бы и `--finalize` master-key никогда бы не сошёлся. Commit прямо здесь
    после успешного swap. На race (0 rows) тоже committ'им — это no-op write,
    но без него транзакция остаётся в open-состоянии; на exception — rollback.
    """
    old_blob = cred.secret_encrypted
    try:
        new_blob = _to_envelope(result.plaintext, cred.id)
        swapped = await repo.cas_update_secret_encrypted(
            db,
            cred_id=cred.id,
            expected_blob=old_blob,
            new_blob=new_blob,
        )
    except Exception as exc:  # noqa: BLE001
        # Lazy re-encrypt — best-effort. Падать ради миграции мы не имеем
        # права: read должен отдать plaintext caller'у. Следующий reveal
        # либо повторит попытку, либо обнаружит, что строка уже мигрирована.
        # Сессию не rollback'им сами: caller's `get_db` ловит выход из
        # контекст-менеджера и закроет сессию там (любое 0-row write
        # автоматически откатится при close без commit'а).
        logger.warning(
            "lazy_reencrypt_failed cred_id=%s source_v=%s err=%s",
            cred.id,
            result.source_version,
            type(exc).__name__,
        )
        return

    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        # Commit упал — read должен пройти, миграция повторится на
        # следующем reveal'е. Откат сделает caller's close.
        logger.warning(
            "lazy_reencrypt_commit_failed cred_id=%s source_v=%s err=%s",
            cred.id,
            result.source_version,
            type(exc).__name__,
        )
        return

    if not swapped:
        # Конкурент опередил (другой reveal / PATCH). Дублируем как INFO —
        # это ожидаемая race, не баг.
        logger.info(
            "lazy_reencrypt_race cred_id=%s source_v=%s (row already migrated)",
            cred.id,
            result.source_version,
        )


def _as_utc(value: datetime) -> datetime:
    """Naive → UTC, aware → astimezone(UTC). БД отдаёт timestamptz, но fixture'ы
    в тестах иногда суют naive datetime; нормализуем тут, чтобы сравнение не
    кидало TypeError.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _check_validity_or_raise(cred: Credential) -> None:
    """410 GONE если `now` вне окна [valid_from, valid_to]. Используется только
    в reveal'е — metadata GET'ы остаются доступны, чтобы UI мог показать
    "продлите токен"."""
    now = datetime.now(timezone.utc)
    if cred.valid_from is not None and _as_utc(cred.valid_from) > now:
        raise GoneError(
            error_code="SECRET_NOT_YET_VALID",
            message="Credential is not valid yet; check valid_from",
            details={"valid_from": _as_utc(cred.valid_from).isoformat()},
        )
    if cred.valid_to is not None and _as_utc(cred.valid_to) < now:
        raise GoneError(
            error_code="SECRET_EXPIRED",
            message="Credential has expired; renew or extend valid_to",
            details={"valid_to": _as_utc(cred.valid_to).isoformat()},
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


def _is_service_admin_for(identity: Identity, cred: Credential) -> bool:
    """admin secret_service'а с правом действовать над `cred` — only own dept.

    Cred сидит в dept'е actor'а, если:
      * `cred.owner_dept_id == identity.department_id` (department / cross_dep), или
      * `cred.owner_user_dept_id == identity.department_id` (personal владельца
        того же dept'а; пустой owner_user_dept_id — допуск как best-effort).
    Cross-dept привилегий у роли нет — admin dep_A не лезет в cred'ы dep_B.
    """
    if not _is_service_admin(identity):
        return False
    if identity.department_id is None:
        return False
    if cred.owner_dept_id is not None and cred.owner_dept_id == identity.department_id:
        return True
    if cred.scope == "personal":
        owner_dept = cred.owner_user_dept_id
        if owner_dept is None or owner_dept == identity.department_id:
            return True
    return False


def _is_account_admin(identity: Identity) -> bool:
    return identity.platform_role == "account_admin"


def is_guest_only(identity: Identity) -> bool:
    """Guest = носитель ТОЛЬКО роли `guest` в secret_service.

    Если у actor'а есть ещё какая-то роль (reader/operator/admin) — он не
    guest, идёт обычным путём. Чистый guest получает урезанный listing
    (только id/name/service/scope/visible_to_dept) и больше ничего.
    """
    roles = identity.roles_for(SERVICE_NAME)
    return bool(roles) and all(r == "guest" for r in roles)


# Internal alias — для краткости в этом модуле.
_is_guest_only = is_guest_only


async def create(
    db: AsyncSession, identity: Identity, payload: CredentialCreate
) -> Credential:
    """Создать креду. Personal → owner = identity, остальные → owner_dept_id из body.

    Любая ошибка на пути (denied/conflict/integrity) эмитит
    `tokens.create / failure` ДО raise — SOC должен видеть провалившиеся
    попытки создания.
    """
    cred_id = _new_cred_id()
    try:
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
            # На свой dep заводит обычный operator+ (auth+role-каталог уже отсеяли
            # guest'ов на endpoint-level). На чужой dep — только account_admin:
            # у `admin` secret_service'а cross-dept привилегий нет, он живёт
            # per-(dept, service).
            if identity.department_id != owner_dept_id:
                if not _is_account_admin(identity):
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

        envelope = _to_envelope(payload.secret, cred_id)
        # Denormalize owner's dept на personal-кред, чтобы access-check мог
        # отбивать ACL'и из чужих dep'ов без обратного запроса в auth_service.
        owner_user_dept_id: str | None = (
            identity.department_id if payload.scope == "personal" else None
        )
        try:
            cred = await repo.create(
                db,
                id=cred_id,
                name=payload.name,
                service=payload.service,
                scope=payload.scope,
                owner_user_id=owner_user_id,
                owner_dept_id=owner_dept_id,
                owner_user_dept_id=owner_user_dept_id,
                login=payload.login,
                secret_encrypted=envelope,
                status="active",
                created_by=identity.user_id,
                visible_to_dept=payload.visible_to_dept,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
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
    except Exception as exc:
        _emit_action_failure(
            "tokens.create",
            target_id=cred_id,
            target_type="credential",
            exc=exc,
            extra={"scope": payload.scope, "service": payload.service, "name": payload.name},
        )
        raise

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

    Guest-role: отдельный путь. Видит только `visible_to_dept=True` AND
    `owner_dept_id == identity.department_id` (своего dep'а). Никаких ACL,
    никаких DeptGrant'ов, никаких personal. Caller-endpoint должен сериализовать
    через CredentialGuestRead (без metadata).
    """
    if _is_guest_only(identity):
        if not identity.department_id:
            return [], None
        dept_creds = await repo.list_for_dept(
            db,
            identity.department_id,
            scope=scope,
            status=status,
            limit=limit * 2,
            cursor=cursor,
        )
        guest_visible = [c for c in dept_creds if c.visible_to_dept]
        guest_visible.sort(key=lambda c: (c.created_at, c.id), reverse=True)
        sliced = guest_visible[:limit]
        next_cursor = None
        if len(guest_visible) > limit:
            last = sliced[-1]
            next_cursor = f"{last.created_at.isoformat()}|{last.id}"
        return sliced, next_cursor

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

        # cross_department recipient: cred'ы из чужих dep'ов, на которые наш
        # dep имеет DeptGrant + RoleACL (can_read). Раньше эта ветка только
        # обещалась в docstring'е, но фактически не делалась — recipient'ы не
        # видели cross-dep cred в листе.
        seen = {c.id for c in visible}
        role_names = identity.roles_for(SERVICE_NAME) or []
        if role_names:
            grant_subq = (
                select(DeptGrant.id)
                .where(
                    DeptGrant.cred_id == Credential.id,
                    DeptGrant.recipient_dept_id == identity.department_id,
                )
            )
            acl_subq = (
                select(RoleACL.id)
                .where(
                    RoleACL.cred_id == Credential.id,
                    RoleACL.dept_id == identity.department_id,
                    RoleACL.role_name.in_(role_names),
                    RoleACL.can_read.is_(True),
                )
            )
            stmt = (
                select(Credential)
                .where(
                    Credential.scope == "cross_department",
                    Credential.owner_dept_id != identity.department_id,
                    exists(grant_subq),
                    exists(acl_subq),
                )
            )
            if scope is not None:
                stmt = stmt.where(Credential.scope == scope)
            if status is not None:
                stmt = stmt.where(Credential.status == status)
            if cursor is not None:
                after_created_at, after_id = cursor
                stmt = stmt.where(
                    or_(
                        Credential.created_at < after_created_at,
                        and_(
                            Credential.created_at == after_created_at,
                            Credential.id < after_id,
                        ),
                    )
                )
            stmt = stmt.order_by(
                Credential.created_at.desc(), Credential.id.desc()
            ).limit(limit * 2)
            cross_creds = list((await db.execute(stmt)).scalars())
            for cred in cross_creds:
                if cred.id in seen:
                    continue
                # check_access всё равно — там сидят дополнительные правила
                # (blocked, can_read и т.п.). SQL-precheck лишь сужает выборку.
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

    Раньше функция временно подменяла `cred.status = "active"` — это создавало
    окно autoflush'а, в котором другие транзакции могли увидеть active. Теперь
    спрашиваем check_access через `status_override`, ORM-объект не трогаем.
    """
    allowed, _reason = await access_service.check_access(
        db, identity, cred, "read", status_override="active"
    )
    return allowed


# Reasons, которые означают «cred вне зоны видимости actor'а» — отдаём 404
# (info-leak protection). Остальные denied-reasons означают «cred в зоне
# видимости, но конкретного права нет» — отдаём 403.
#
# `guest_role_no_access` — guest на прямом GET известного cred_id; раньше
# отвечали 403, что позволяло перебирать ID-шники чужих кред с
# `visible_to_dept=False`. Маскируем под 404.
_NOT_VISIBLE_REASONS: frozenset[str] = frozenset({
    "scope_mismatch",
    "dept_grant_missing",
    "guest_role_no_access",
})


async def load_for_action(
    db: AsyncSession, identity: Identity, cred_id: str, action: str
) -> Credential:
    """SELECT по PK + access-check. NotFound → 404, blocked → 410 для тех,
    кто имел бы read-доступ на active; для остальных — 404 (info-leak).

    На любой denied-ветке (404 info-leak, 410 blocked-без-доступа, 403) эмитим
    `tokens.access_denied / failure` — иначе SOC не видит попыток
    несанкционированного доступа. `details.reason` различает ветки:
    `info_leak_404` для маскировки в 404, `blocked` — для blocked-cred, иначе
    конкретный reason от `check_access`.

    Защита от brute-force: каждый denied access инкрементит счётчик в
    `lockout_service`; превышение порога — 429 + Retry-After. Перед load'ом
    проверяем существующий lockout и сразу отбиваем 429. На успехе clear'им
    счётчик, чтобы случайные denied'ы не накапливались.
    """
    actor_id = identity.user_id
    if lockout_service.is_locked(actor_id):
        retry_after = lockout_service.retry_after_seconds(actor_id)
        raise RateLimitError(
            error_code="ACTOR_LOCKED_OUT",
            message="Too many denied access attempts; try again later",
            details={"retry_after_seconds": retry_after, "action": action},
        )

    cred = await repo.get_by_id(db, cred_id)
    if cred is None:
        # cred физически отсутствует — это не маскировка прав, audit не нужен.
        raise NotFoundError(
            error_code="CREDENTIAL_NOT_FOUND",
            message="Credential not found",
        )

    allowed, reason = await access_service.check_access(db, identity, cred, action)  # type: ignore[arg-type]
    if not allowed:
        # Incr lockout-counter. True == порог пробит этой попыткой → WARNING.
        lockout_now = lockout_service.record_denied(actor_id)
        if lockout_now:
            audit_service.emit(
                "tokens.lockout_triggered",
                target_id=actor_id,
                target_type="user",
                severity="WARNING",
                details={
                    "action": action,
                    "cred_id": cred.id,
                    "retry_after_seconds": lockout_service.retry_after_seconds(actor_id),
                },
            )
        if reason == "blocked":
            # 410 vs 404: 410 видят те, кто имел бы read-доступ на active;
            # остальные — 404. Audit-emit в обоих случаях, чтобы SOC видел.
            would_see_active = await _would_have_read_access_if_active(
                db, identity, cred
            )
            audit_service.emit(
                "tokens.access_denied",
                target_id=cred.id,
                target_type="credential",
                status="failure",
                allowed=False,
                details={
                    "action": action,
                    "reason": "blocked",
                    "scope": cred.scope,
                    "masked_as": "410" if would_see_active else "404",
                },
            )
            if would_see_active:
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
            # 404-маскировка — audit обязателен, иначе SOC не видит попытку.
            audit_service.emit(
                "tokens.access_denied",
                target_id=cred.id,
                target_type="credential",
                status="failure",
                allowed=False,
                details={
                    "action": action,
                    "reason": "info_leak_404",
                    "scope": cred.scope,
                    "underlying_reason": reason,
                },
            )
            raise NotFoundError(
                error_code="CREDENTIAL_NOT_FOUND",
                message="Credential not found",
            )

        # Реальный 403 — actor видит cred, но конкретного права нет.
        audit_service.emit(
            "tokens.access_denied",
            target_id=cred.id,
            target_type="credential",
            status="failure",
            allowed=False,
            details={"action": action, "reason": reason, "scope": cred.scope},
        )
        raise AuthorizationError(
            error_code="CREDENTIAL_ACCESS_DENIED",
            message=f"Access denied for action={action!r}",
            details={"reason": reason},
        )

    # Доступ есть; если cred blocked И action != read — отдаём 410 явно.
    if cred.status == "blocked" and action != "read" and action != "manage_status":
        _ensure_active_or_raise(cred)
    # Успешный access сбрасывает lockout-счётчик: одна случайная denied-попытка
    # не накапливается до бесконечности.
    lockout_service.clear(actor_id)
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
    """PATCH name/login/secret. Secret меняется → re-encrypt.

    На любой ошибке (access denied / not found / conflict / integrity) эмитим
    `tokens.update / failure` ДО raise. `load_for_action` сам пишет
    `tokens.access_denied`; здесь добавляем явный update-failure event, чтобы
    SOC видел действие в правильной категории.
    """
    fields_keys: list[str] = []
    try:
        cred = await load_for_action(db, identity, cred_id, "write")

        fields: dict = {}
        if payload.name is not None:
            fields["name"] = payload.name
        if payload.login is not None:
            fields["login"] = payload.login
        if payload.secret is not None:
            fields["secret_encrypted"] = _to_envelope(payload.secret, cred.id)
        if payload.valid_from is not None:
            fields["valid_from"] = payload.valid_from
        if payload.valid_to is not None:
            fields["valid_to"] = payload.valid_to

        if not fields:
            return cred

        fields_keys = sorted(fields.keys())
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
    except Exception as exc:
        _emit_action_failure(
            "tokens.update",
            target_id=cred_id,
            target_type="credential",
            exc=exc,
            extra={"fields": fields_keys} if fields_keys else None,
        )
        raise

    audit_service.emit(
        "tokens.update",
        target_id=cred.id,
        target_type="credential",
        details={"fields": fields_keys, "changed_fields": fields_keys},
    )
    return cred


async def delete(
    db: AsyncSession,
    identity: Identity,
    cred_id: str,
    payload: AdminDeleteRequest | None,
) -> None:
    """DELETE кред. Admin override (admin своего dept'а не-owner) требует reason.

    account_admin к delete не подпущен: платформенный админ не имеет права
    удалять секреты — это зона dept-уровня (см. Memory: project-dbos-secrets-scope).

    На failure-пути эмитим `tokens.delete / failure` (или
    `tokens.admin_override_delete / failure`, если ветка override уже
    определена) ДО raise. `tokens.access_denied` остаётся как было —
    это отдельный compliance-канал.
    """
    is_admin_override = False
    try:
        cred = await repo.get_by_id(db, cred_id)
        if cred is None:
            raise NotFoundError(
                error_code="CREDENTIAL_NOT_FOUND",
                message="Credential not found",
            )

        # Проверка: владелец / dep_admin owner_dep / service-admin своего dept'а.
        # account_admin плоско отрезан: платформенный админ не имеет права
        # удалять чужие cred'ы и не должен видеть plaintext — управление
        # секретами per-dept (см. Memory: project-dbos-secrets-scope). Если
        # owner_dep удалён, восстановление идёт через lifecycle_service, не через
        # admin-override.
        is_owner = (
            cred.scope == "personal"
            and identity.actor_type == "user"
            and cred.owner_user_id == identity.user_id
        )
        if not is_owner:
            # Попробуем разрешить как dep_admin owner_dep / service-admin своего dept'а.
            allowed, reason = await access_service.check_access(
                db, identity, cred, "delete"
            )
            if allowed:
                pass  # legitimate owner-side delete (dep_admin/admin внутри scope)
            elif _is_service_admin_for(identity, cred):
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
    except Exception as exc:
        action = "tokens.admin_override_delete" if is_admin_override else "tokens.delete"
        _emit_action_failure(
            action,
            target_id=cred_id,
            target_type="credential",
            exc=exc,
        )
        raise

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
    # Окно валидности проверяется ПОСЛЕ access-check, но ДО decrypt'а: actor
    # с доступом, который запросил истёкший токен, получает понятный
    # SECRET_EXPIRED / SECRET_NOT_YET_VALID; неавторизованный — обычный 404/403.
    try:
        _check_validity_or_raise(cred)
    except GoneError as exc:
        audit_service.emit(
            "tokens.revealed_blocked_by_validity",
            target_id=cred.id,
            target_type="credential",
            status="failure",
            allowed=False,
            details={
                "error_code": exc.error_code,
                "scope": cred.scope,
                "service": cred.service,
                **exc.details,
            },
        )
        raise
    decrypt_result = _decrypt_envelope_with_meta(cred.secret_encrypted, cred.id)
    plaintext = decrypt_result.plaintext
    if decrypt_result.needs_reencrypt:
        await _lazy_reencrypt_if_needed(db, cred, decrypt_result)
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
    """Transfer ownership. Только для blocked-кред.

    Два пути входа:

    * admin secret_service'а владеющего dept'а — штатный путь. Роль
      per-(dept, service), cross-dept привилегий не даёт.
    * account_admin — emergency-override. Нужен, когда владеющий отдел удалён
      и живого service-admin'а у него уже нет: иначе blocked-кред'а ушла бы в
      hard-delete по sweep'у. Платформенный админ переназначает владельца на
      указанный в теле `new_owner_user_id`/`new_owner_dept_id` (ровно один, как
      и в штатном пути). Это узкий путь именно для transfer/recover — обычный
      CRUD/reveal для account_admin остаётся закрыт.

    FOR UPDATE на cred: два параллельных transfer'а на одну креду читают одну
    и ту же blocked-строку и оба пишут разный owner — без локa последний
    UPDATE побеждает и owner становится непредсказуемым.

    IntegrityError на partial UNIQUE — переводим в 409 NAME_DUPLICATE; иначе
    aborted-state соединения роняет endpoint 500'кой.
    """
    is_account_admin_override = False
    try:
        cred = await repo.get_by_id_for_update(db, cred_id)
        if cred is None:
            raise NotFoundError(
                error_code="CREDENTIAL_NOT_FOUND",
                message="Credential not found",
            )

        if _is_service_admin_for(identity, cred):
            pass
        elif _is_account_admin(identity):
            is_account_admin_override = True
        else:
            raise AuthorizationError(
                error_code="CREDENTIAL_ACCESS_DENIED",
                message="Only secret_service admin of the owning department or account_admin can transfer ownership",
            )

        if cred.status != "blocked":
            raise DomainValidationError(
                error_code="CREDENTIAL_NOT_BLOCKED",
                message="Transfer is only allowed for blocked credentials",
            )

        old_owner_user_id = cred.owner_user_id
        old_owner_dept_id = cred.owner_dept_id

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
        try:
            await db.flush()
            await db.commit()
            await db.refresh(cred)
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                error_code="NAME_DUPLICATE",
                message="Credential name conflict on transfer (race or duplicate target)",
            ) from exc
    except Exception as exc:
        _emit_action_failure(
            "tokens.transfer_ownership",
            target_id=cred_id,
            target_type="credential",
            exc=exc,
        )
        raise

    audit_service.emit(
        "tokens.transfer_ownership",
        target_id=cred.id,
        target_type="credential",
        details={
            "scope": cred.scope,
            "old_owner_user_id": old_owner_user_id,
            "old_owner_dept_id": old_owner_dept_id,
            "new_owner_user_id": cred.owner_user_id,
            "new_owner_dept_id": cred.owner_dept_id,
            "reason": payload.reason,
            "actor_role": "account_admin" if is_account_admin_override else "service_admin",
            "emergency_override": is_account_admin_override,
        },
    )
    return cred


async def recover(
    db: AsyncSession, identity: Identity, cred_id: str
) -> Credential:
    """Recover blocked-кред. Окно — 30 дней с момента блокировки.

    Два пути входа, как у transfer:

    * admin secret_service'а владеющего dept'а — штатный путь (per-(dept,
      service) роль, cross-dept привилегий не даёт);
    * account_admin — emergency-override для кред'ы с удалённым владеющим
      отделом, у которого нет живого service-admin'а. Снимает блокировку в
      пределах recover-окна. Узкий путь: обычный CRUD/reveal для account_admin
      остаётся закрыт.

    FOR UPDATE + try/except IntegrityError — симметрия с transfer; параллельный
    recover/transfer на одну креду не должен ронять 500.
    """
    is_account_admin_override = False
    try:
        cred = await repo.get_by_id_for_update(db, cred_id)
        if cred is None:
            raise NotFoundError(
                error_code="CREDENTIAL_NOT_FOUND",
                message="Credential not found",
            )
        if _is_service_admin_for(identity, cred):
            pass
        elif _is_account_admin(identity):
            is_account_admin_override = True
        else:
            raise AuthorizationError(
                error_code="CREDENTIAL_ACCESS_DENIED",
                message="Only secret_service admin of the owning department or account_admin can recover credential",
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

        try:
            cred = await repo.mark_active(db, cred)
            await db.commit()
            await db.refresh(cred)
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                error_code="NAME_DUPLICATE",
                message="Credential name conflict on recover (active duplicate exists)",
            ) from exc
    except Exception as exc:
        _emit_action_failure(
            "tokens.recover",
            target_id=cred_id,
            target_type="credential",
            exc=exc,
        )
        raise

    audit_service.emit(
        "tokens.recover",
        target_id=cred.id,
        target_type="credential",
        details={
            "scope": cred.scope,
            "service": cred.service,
            "actor_role": "account_admin" if is_account_admin_override else "service_admin",
            "emergency_override": is_account_admin_override,
        },
    )
    return cred
