"""OAuth2: CRUD клиентов + authorization_code flow + client_credentials grant.

Поддерживаем RFC 6749 + RFC 7636 (PKCE). Implicit и hybrid сознательно отказаны
(см. endpoints/oauth2.py для обоснования). PKCE опционален для confidential
клиентов, обязателен для public (SPA/CLI).
"""

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import PlatformRole, UserStatus
from src.core.exceptions import AuthenticationError, AuthorizationError, ConflictError, NotFoundError
from src.core.security import (
    create_access_token,
    generate_oauth_refresh_token,
    hash_opaque_token,
)
from src.repositories.departments import DepartmentRepository
from src.repositories.oauth_clients import OAuthClientRepository, OAuthCodeRepository
from src.repositories.oauth_refresh_tokens import OAuthRefreshTokenRepository
from src.repositories.users import UserRepository
from src.schemas.oauth import (
    OAuthClientCreate,
    OAuthClientCreatedResponse,
    OAuthClientResponse,
    OAuthTokenResponse,
)
from src.services import _lockout, audit_service
from src.services._cache_invalidation import invalidate_identity_cache as _invalidate_identity_cache
from src.services._dept_guard import assert_actor_exists, assert_dept_admin_target_dept
from src.utils.time import is_expired, utcnow

_SECRET_PREFIX_LEN = 12

# RFC 7636 §4.3: code_challenge_method ∈ {"S256", "plain"}. "plain" разрешён
# спецификацией, но S256 обязателен для public client'ов — мы принимаем оба
# на стороне сервера (валидируем method), решение оставляем за клиентом.
_PKCE_METHODS = {"S256", "plain"}

logger = logging.getLogger(__name__)


def _to_response(client) -> OAuthClientResponse:
    """ORM-клиент → OAuthClientResponse DTO (без plaintext secret)."""
    return OAuthClientResponse(
        id=client.id,
        client_id=client.client_id,
        department_id=client.department_id,
        name=client.name,
        description=client.description,
        redirect_uris=client.redirect_uris,
        allowed_scopes=client.allowed_scopes,
        grant_types=client.grant_types,
        is_active=client.is_active,
        is_public=client.is_public,
        created_at=client.created_at,
    )


async def create_client(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    data: OAuthClientCreate,
    request_id: str | None = None,
) -> OAuthClientCreatedResponse:
    """Создать OAuth2-клиента. Plaintext secret возвращается один раз."""
    dept_repo = DepartmentRepository(db)
    client_repo = OAuthClientRepository(db)
    user_repo = UserRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        await assert_dept_admin_target_dept(
            user_repo, actor_id, data.department_id,
            error_code="DEPARTMENT_ACCESS_DENIED",
            message="department_admin can only create OAuth2 clients for their own department",
        )

    dept = await dept_repo.get_by_id(data.department_id)
    if dept is None:
        raise NotFoundError(error_code="DEPARTMENT_NOT_FOUND", message="Department not found")

    if await client_repo.exists_name(data.department_id, data.name):
        raise ConflictError(
            error_code="OAUTH_CLIENT_NAME_EXISTS",
            message=f"OAuth2 client '{data.name}' already exists in this department",
        )

    # Public-клиенты (SPA / native CLI) не могут безопасно хранить
    # client_secret — для них identity доказывается PKCE-verifier'ом,
    # секрет не выдаём. В БД на месте `client_secret_hash` лежит пустая
    # строка (NOT NULL), `client_secret_prefix` тоже пуст — `exchange_code`
    # для public пропускает verify целиком.
    if data.is_public:
        raw_secret: str | None = None
        secret_hash = ""
        secret_prefix = ""
    else:
        raw_secret = f"cs_{secrets.token_urlsafe(32)}"
        secret_hash = hash_opaque_token(raw_secret)
        secret_prefix = raw_secret[:_SECRET_PREFIX_LEN]

    client = await client_repo.create(
        department_id=data.department_id,
        name=data.name,
        client_secret_hash=secret_hash,
        client_secret_prefix=secret_prefix,
        redirect_uris=data.redirect_uris,
        allowed_scopes=data.allowed_scopes,
        grant_types=data.grant_types,
        description=data.description,
        is_public=data.is_public,
        created_by=actor_id,
    )
    await db.commit()
    # `client_secret` plaintext в details не пишем — оставляем только prefix.
    # Redaction sanitizer всё равно бы его смаскировал, но это implicit guard;
    # явный отказ от plaintext в audit-канале надёжнее (defence-in-depth).
    audit_service.emit(
        "oauth_client.create", actor_id, target_id=client.id, target_type="oauth_client",
        request_id=request_id,
        details={
            "name": data.name,
            "department_id": data.department_id,
            "department_name": dept.name,
            "redirect_uris": list(data.redirect_uris),
            "allowed_scopes": list(data.allowed_scopes),
            "grant_types": list(data.grant_types),
            "client_secret_prefix": secret_prefix,
            "description": data.description,
            "is_public": data.is_public,
        },
    )
    return OAuthClientCreatedResponse(**_to_response(client).model_dump(), client_secret=raw_secret)


async def list_clients(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    department_id: str | None = None,
    request_id: str | None = None,
) -> list[OAuthClientResponse]:
    """Список клиентов с учётом scope-а смотрящего."""
    client_repo = OAuthClientRepository(db)
    user_repo = UserRepository(db)

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        # Fail-closed: пустой список вместо `ACTOR_VANISHED` молча скрывал бы
        # race-delete actor'а — write-операции (`create_client`/`delete_client`)
        # уже отдают 403 ACTOR_VANISHED через `assert_dept_admin_target_dept`,
        # read должен вести себя симметрично.
        actor = await assert_actor_exists(user_repo, actor_id)
        dept_id = actor.department_id
        clients = await client_repo.list_by_department(dept_id) if dept_id else []
    elif department_id:
        clients = await client_repo.list_by_department(department_id)
    else:
        clients = await client_repo.list_all()

    audit_service.emit(
        "oauth_client.list", actor_id, status="success", allowed=True,
        request_id=request_id,
        details={
            "count": len(clients),
            "filter_department_id": department_id,
            "scope": "department" if (actor_role == PlatformRole.DEPARTMENT_ADMIN or department_id) else "all",
        },
    )
    return [_to_response(c) for c in clients]


async def delete_client(
    db: AsyncSession,
    actor_id: str,
    actor_role: str | None,
    client_db_id: str,
    request_id: str | None = None,
) -> None:
    """Soft-delete клиента (`is_active=False`). История auth-кодов сохраняется."""
    client_repo = OAuthClientRepository(db)
    user_repo = UserRepository(db)

    client = await client_repo.get_by_id(client_db_id)
    if client is None:
        raise NotFoundError(error_code="OAUTH_CLIENT_NOT_FOUND", message="OAuth2 client not found")

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        await assert_dept_admin_target_dept(
            user_repo, actor_id, client.department_id,
            error_code="DEPARTMENT_ACCESS_DENIED",
            message="Cannot delete OAuth2 client outside your department",
        )

    await client_repo.deactivate(client)
    await db.commit()
    # Identity-кэш ключуется по `IdentityContext.user_id`, а для
    # client_credentials туда уезжает `client.client_id` (см.
    # `dependencies/auth._identity_from_oauth_client_jwt`). Без этого сброса
    # старый m2m-JWT продолжал бы проходить `get_current_identity` до
    # истечения TTL — симметрично user-side ban/role-change.
    _invalidate_identity_cache(client.client_id)
    audit_service.emit(
        "oauth_client.delete", actor_id, target_id=client_db_id, target_type="oauth_client",
        request_id=request_id,
        details={
            "name": client.name,
            "client_id": client.client_id,
            "department_id": client.department_id,
        },
    )


# ── Authorization code flow ───────────────────────────────────────────────────

def _verify_pkce(code_challenge: str, code_challenge_method: str, verifier: str) -> bool:
    """Проверить PKCE-verifier против сохранённого challenge.

    RFC 7636 §4.6:
    * S256: BASE64URL-ENCODE(SHA256(ASCII(verifier))) == challenge (без `=`-padding).
    * plain: verifier == challenge.

    Сравнение через `hmac.compare_digest` — constant-time, защита от timing-oracle.
    """
    method = (code_challenge_method or "plain")
    if method == "S256":
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return hmac.compare_digest(computed, code_challenge)
    if method == "plain":
        return hmac.compare_digest(verifier, code_challenge)
    # Unknown method сюда не должен доходить — `issue_authorization_code`
    # режет method'ы вне `_PKCE_METHODS`. На всякий случай — отказ.
    return False


async def issue_authorization_code(
    db: AsyncSession,
    client_id: str,
    user_id: str,
    redirect_uri: str,
    scopes: list[str],
    request_id: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
) -> str:
    """Валидировать клиента и выдать raw authorization code.

    PKCE (RFC 7636): если задан `code_challenge` — сохраняем его вместе с
    method'ом (default `plain`); на `/token` обмен потребует `code_verifier`.
    Если challenge нет — допустимо (legacy confidential-client с client_secret).
    """
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(client_id)
    if client is None or not client.is_active:
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Unknown or inactive OAuth2 client")

    if "authorization_code" not in client.grant_types:
        raise AuthorizationError(error_code="GRANT_TYPE_NOT_ALLOWED", message="authorization_code grant not enabled for this client")

    if redirect_uri not in client.redirect_uris:
        raise AuthorizationError(error_code="REDIRECT_URI_MISMATCH", message="redirect_uri does not match registered URIs")

    # Без проверки статуса /authorize возвращал 200 даже забаненному юзеру —
    # код выписывался, /token позже отбивал по `OAUTH_USER_INACTIVE`. Само
    # 200 vs ошибка на /authorize — username-enumeration oracle: атакующий,
    # отправляющий заявку от чужого имени, отличает «активен» от «бан».
    # Симметрия с /login, где BANNED/BLOCKED отбиваются сразу.
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if user is None or user.status != UserStatus.ACTIVE or not user.is_active:
        raise AuthenticationError(
            error_code="OAUTH_USER_INACTIVE",
            message="User account is not active",
        )

    # PKCE-валидация. Для public-клиентов (`is_public=True`) S256 обязателен —
    # plain не защищает от перехвата кода (verifier == challenge тривиально).
    # Для confidential — поведение прежнее (back-compat): если challenge есть,
    # method из дозволенного множества; если нет — пропускаем.
    #
    # Отдельная защита: `code_challenge_method` без `code_challenge` —
    # явная клиентская ошибка (RFC 7636 §4.3 method имеет смысл только
    # вместе с challenge). До фикса метод тихо игнорировался, клиент мог
    # годами слать неправильную пару и не узнать.
    if code_challenge_method and not code_challenge:
        raise AuthorizationError(
            error_code="PKCE_CHALLENGE_REQUIRED",
            message="code_challenge_method requires code_challenge",
        )
    pkce_challenge: str | None = None
    pkce_method: str | None = None
    if client.is_public:
        if not code_challenge:
            raise AuthorizationError(
                error_code="PKCE_REQUIRED",
                message="code_challenge is required for public OAuth clients",
            )
        method = code_challenge_method or "plain"
        if method != "S256":
            raise AuthorizationError(
                error_code="PKCE_METHOD_INVALID",
                message="public OAuth clients must use code_challenge_method=S256",
            )
        pkce_challenge = code_challenge
        pkce_method = method
    elif code_challenge:
        method = code_challenge_method or "plain"
        if method not in _PKCE_METHODS:
            raise AuthorizationError(
                error_code="PKCE_METHOD_INVALID",
                message=f"code_challenge_method must be one of {sorted(_PKCE_METHODS)}",
            )
        if method == "plain":
            # Confidential клиент с PKCE plain — формально допустимо по RFC,
            # но фактически бесполезно (verifier == challenge, защиты от
            # перехвата нет). Шлём audit-event (WARNING) — оператор/SIEM
            # должны видеть это вне контейнерных логов; параллельный
            # logger.warning оставляем для локального дебага.
            logger.warning(
                "oauth client %s used PKCE plain (insecure); recommend S256",
                client_id,
            )
            audit_service.emit(
                "oauth.pkce_plain_used",
                user_id,
                target_id=client.id,
                target_type="oauth_client",
                request_id=request_id,
                details={
                    "client_id": client_id,
                    "user_id": user_id,
                },
            )
        pkce_challenge = code_challenge
        pkce_method = method

    effective_scopes = [s for s in scopes if s in client.allowed_scopes]

    raw_code = secrets.token_urlsafe(32)
    code_hash = hash_opaque_token(raw_code)
    settings = get_settings()
    expires_at = utcnow() + timedelta(seconds=settings.oauth_code_ttl_seconds)

    code_repo = OAuthCodeRepository(db)
    await code_repo.create(
        client_id=client_id,
        user_id=user_id,
        code_hash=code_hash,
        redirect_uri=redirect_uri,
        scopes=effective_scopes,
        expires_at=expires_at,
        code_challenge=pkce_challenge,
        code_challenge_method=pkce_method,
    )
    await db.commit()
    audit_service.emit(
        "oauth.authorization_code_issued",
        user_id,
        target_id=client_id,
        target_type="oauth_client",
        status="success",
        allowed=True,
        details={
            "client_id": client_id,
            "client_name": client.name,
            "redirect_uri": redirect_uri,
            "requested_scopes": list(scopes),
            "granted_scopes": effective_scopes,
            "code_ttl_seconds": settings.oauth_code_ttl_seconds,
            "pkce": pkce_method is not None,
            "pkce_method": pkce_method,
        },
        request_id=request_id,
    )
    return raw_code


async def _verify_client_secret_with_lockout(
    db: AsyncSession,
    client_repo: OAuthClientRepository,
    client,
    client_secret: str,
) -> None:
    """Проверить client_secret с lockout-пайплайном.

    Зеркалит `verify_password_with_lockout`: release_if_expired → assert_not_locked
    → compare_digest. При неудаче — атомарный инкремент, при превышении лимита
    — locked_until. Любая неудача поднимает `OAUTH_CLIENT_INVALID` (без
    раскрытия того, что lockout сработал на конкретно этом промахе; залоченный
    клиент получит 429 на следующем запросе через assert_not_locked).

    Транзакция: одиночный commit перед raise — он закрывает и JIT-release
    устаревшего lockout'а, и register_failure'ы (counter + опциональный
    locked_until). При raise SQLAlchemy выполнит rollback незакоммиченного
    состояния, но к моменту вызова compare_digest всё, что должно остаться в
    БД, уже включено в pending changes.
    """
    released_expired = await _lockout.release_principal_if_expired(client_repo, client)

    _lockout.assert_principal_not_locked(client)

    if hmac.compare_digest(hash_opaque_token(client_secret), client.client_secret_hash):
        if released_expired:
            await db.commit()
        return

    settings = get_settings()
    await _lockout.register_principal_failure(
        client_repo,
        client,
        counter_attr="failed_secret_attempts",
        increment_method="increment_failed_secret_attempts",
        max_attempts=settings.oauth_client_max_failed_secret_attempts,
        lockout_minutes=settings.oauth_client_lockout_minutes,
    )
    await db.commit()
    raise AuthenticationError(
        error_code="OAUTH_CLIENT_INVALID",
        message="Invalid client credentials",
    )


async def _issue_refresh_token(
    db: AsyncSession,
    *,
    client_id: str,
    user_id: str,
    scopes: list[str],
) -> str:
    """Создать opaque OAuth refresh-токен, в БД лечь только hash. Вернуть raw.

    Caller отвечает за `db.commit()` — INSERT откатится через `get_db()` иначе.
    """
    settings = get_settings()
    raw_refresh, refresh_hash = generate_oauth_refresh_token()
    expires = utcnow() + timedelta(days=settings.oauth_refresh_token_ttl_days)
    refresh_repo = OAuthRefreshTokenRepository(db)
    await refresh_repo.create(
        client_id=client_id,
        user_id=user_id,
        refresh_token_hash=refresh_hash,
        scopes=list(scopes),
        expires_at=expires,
    )
    return raw_refresh


def _build_oauth_access_token(user_id: str, client_id: str, scopes: list[str], ttl: timedelta) -> str:
    """OAuth access-JWT с минимальным payload (как в `exchange_code`).

    `oauth_scopes` фиксирует approved scope'ы для INTERSECT на introspect'е.
    """
    return create_access_token(
        payload={
            "sub": user_id,
            "actor_type": "user",
            "oauth_client_id": client_id,
            "oauth_scopes": list(scopes),
        },
        expires_delta=ttl,
    )


async def exchange_code(
    db: AsyncSession,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    request_id: str | None = None,
    code_verifier: str | None = None,
) -> OAuthTokenResponse:
    """Обменять authorization code на access_token.

    PKCE (RFC 7636 §4.6): если в выданном коде есть `code_challenge`, требуем
    `code_verifier` в запросе и сверяем. Любая ошибка (missing verifier /
    mismatch) — `401 INVALID_GRANT`.
    """
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(client_id)
    if client is None or not client.is_active:
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    # Public-клиенты: identity доказывается PKCE-verifier'ом, не secret'ом.
    # Confidential — обычный pipeline с lockout. Любой переданный secret для
    # public игнорируется, отдельной ошибки не отдаём — это позволяет
    # фронту слать единый payload без знания типа клиента.
    if not client.is_public:
        await _verify_client_secret_with_lockout(db, client_repo, client, client_secret)
        if client.failed_secret_attempts:
            await client_repo.reset_failed_attempts(client)
            # Reset фиксируем сразу: дальше идут pre-CAS проверки (code lookup,
            # PKCE, redirect_uri), любая из которых может поднять exception
            # и спровоцировать rollback в `get_db()`. Без явного commit
            # счётчик неудач не обнулится, легитимный успешный verify зря
            # пропадёт. Зеркало `client_credentials_token`.
            await db.commit()

    code_hash = hash_opaque_token(code)
    code_repo = OAuthCodeRepository(db)
    auth_code = await code_repo.get_by_hash(code_hash)
    if auth_code is None:
        raise AuthenticationError(error_code="OAUTH_CODE_INVALID", message="Authorization code is invalid or already used")

    # RFC 6749 §4.1.3: код принадлежит выдавшему клиенту. Без этой проверки
    # перехваченный код можно обменять, подставив чужой client_id (например
    # public-клиента без секрета) в обход client-аутентификации. Проверяем до
    # consume, чтобы чужая попытка не сжигала код легитимного клиента.
    if auth_code.client_id != client_id:
        raise AuthenticationError(
            error_code="INVALID_GRANT",
            message="Authorization code was not issued to this client",
        )

    if is_expired(auth_code.expires_at):
        raise AuthenticationError(error_code="OAUTH_CODE_EXPIRED", message="Authorization code has expired")

    if auth_code.redirect_uri != redirect_uri:
        raise AuthorizationError(error_code="REDIRECT_URI_MISMATCH", message="redirect_uri mismatch")

    # PKCE-проверка (RFC 7636 §4.6). Если код выпускался с challenge —
    # verifier обязателен и должен совпасть; ошибка → `INVALID_GRANT` (RFC 6749).
    if auth_code.code_challenge is not None:
        if not code_verifier:
            raise AuthenticationError(
                error_code="INVALID_GRANT",
                message="code_verifier is required for this authorization code (PKCE)",
            )
        if not _verify_pkce(
            auth_code.code_challenge,
            auth_code.code_challenge_method or "plain",
            code_verifier,
        ):
            raise AuthenticationError(
                error_code="INVALID_GRANT",
                message="code_verifier does not match code_challenge (PKCE)",
            )

    # CAS-consume (RFC 6749 §4.1.2 — "authorization code MUST be short-lived
    # and single-use"). Порядок задаёт инвариант повторного обмена:
    #   * Любая pre-consume проверка (client-owner, expiry, redirect_uri, PKCE)
    #     рейзит ДО mark_used, commit'а ещё не было → rollback в get_db()
    #     оставляет used_at = NULL. Тот же код с исправленным запросом
    #     (например верным verifier'ом) обменивается заново — ошибка verifier'а
    #     код не сжигает.
    #   * Успешный mark_used + commit ниже выставляют used_at безвозвратно:
    #     повторный обмен того же кода (даже с верным verifier/secret) ловит
    #     `used_at IS NULL`-фильтр в get_by_hash → OAUTH_CODE_INVALID. One-shot.
    # Consume идёт и ДО работы по выписке JWT (role/dept lookup), иначе при
    # гонке winner+loser потратят CPU зря. Детали — в
    # `OAuthCodeRepository.mark_used`.
    if not await code_repo.mark_used(auth_code):
        # Кто-то уже consume'нул этот код параллельно (другой /token-запрос
        # с тем же `code` пришёл первым). Не reuse-attack в строгом смысле —
        # OAuth-replay: атакующий пытается обменять перехваченный код, пока
        # legit-client тоже обменивает. RFC 6749 §5.2 → `invalid_grant`.
        raise AuthenticationError(
            error_code="INVALID_GRANT",
            message="Authorization code is invalid or already used",
        )
    # Single-use инвариант RFC 6749 §4.1.2: код должен пометиться used сразу,
    # независимо от исхода последующих проверок (user_not_found / inactive).
    # Без явного commit'а raise в pre-JWT-проверках откатывает mark_used
    # через rollback в get_db(), и тот же код можно обменять повторно.
    await db.commit()

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(auth_code.user_id)
    if user is None:
        raise AuthenticationError(error_code="OAUTH_USER_NOT_FOUND", message="User no longer exists")
    # Окно между /authorize и /token: код выписан live-юзеру, но к моменту
    # обмена админ мог его забанить/заблокировать. Без проверки code остаётся
    # валидным до собственного expiry — third-party app получает JWT, который
    # сразу же отбьётся introspect'ом, но сам факт обмена «помогает» атакующему
    # узнать, что юзер существует. Симметрично login-флоу, где banned/blocked
    # отбиваются прямо в /login.
    if user.status != UserStatus.ACTIVE or not user.is_active:
        raise AuthenticationError(
            error_code="OAUTH_USER_INACTIVE",
            message="User account is not active",
        )

    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    # `oauth_scopes` фиксирует ровно те scope'ы, которые юзер аппрувнул на
    # /authorize → они выписаны клиенту в этом auth_code. При revalidate
    # `authorization_service.introspect` пересекает live-права юзера с этим
    # снапшотом, иначе токен с узким scope откроет полные права юзера
    # (scope-creep). `None` отличает не-OAuth JWT от OAuth-с-пустыми-scopes
    # (последнее = «вообще ничего»).
    oauth_scopes = list(auth_code.scopes)
    # Payload намеренно минимален: только `sub` + `actor_type` + OAuth-метки.
    # Username/department/platform_role/allowed_services/service_roles
    # пересчитываются introspect'ом из БД на каждый запрос — иначе JWT
    # без подписи (`base64url`) раскрывает PII и привилегии юзера при утечке.
    # Refresh (если у клиента есть grant `refresh_token`) выдаём ДО сборки JWT:
    # его INSERT нужно закоммитить, а между выпиской access-токена и audit'ом
    # commit'а быть не должно (JWT stateless, в БД не пишется). mark_used-commit
    # был выше — этот commit покрывает только refresh-строку.
    raw_refresh: str | None = None
    if "refresh_token" in client.grant_types:
        raw_refresh = await _issue_refresh_token(
            db, client_id=client_id, user_id=user.id, scopes=oauth_scopes
        )
        await db.commit()
    access_token = _build_oauth_access_token(user.id, client_id, oauth_scopes, ttl)
    audit_service.emit(
        "oauth.code_exchanged",
        user.id,
        target_id=client_id,
        target_type="oauth_client",
        status="success",
        allowed=True,
        details={
            "client_id": client_id,
            "client_name": client.name,
            "username": user.username,
            "department_id": user.department_id,
            "scopes": list(auth_code.scopes),
            "redirect_uri": redirect_uri,
            "ttl_seconds": int(ttl.total_seconds()),
            "refresh_issued": raw_refresh is not None,
        },
        request_id=request_id,
    )
    return OAuthTokenResponse(
        access_token=access_token,
        expires_in=int(ttl.total_seconds()),
        scope=" ".join(auth_code.scopes),
        refresh_token=raw_refresh,
    )


async def client_credentials_token(
    db: AsyncSession,
    client_id: str,
    client_secret: str,
    request_id: str | None = None,
) -> OAuthTokenResponse:
    """Выдать access_token по client_credentials grant (machine-to-machine, без user_id).

    Mutating writes:
        * `_verify_client_secret_with_lockout` инкрементит `failed_secret_attempts`
          и при превышении лимита ставит `locked_until` — успешные/неуспешные
          попытки оба идут с `db.commit()` внутри хелпера.
        * После успешной верификации, если счётчик был ненулевой, тут же
          ресетим его через `reset_failed_attempts` + `db.commit()`.

    Чего нет — usage-tracking-полей в самом `OAuthClient` (`last_token_issued_at`,
    счётчик выпущенных токенов и т.п.); добавишь — не забудь свой
    `await db.commit()`, иначе update уйдёт в rollback через `get_db()`.
    """
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(client_id)
    if client is None or not client.is_active:
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    # client_credentials — m2m grant, без user-flow. Public-клиент (SPA/CLI)
    # не может его использовать: у него нет client_secret для аутентификации
    # себя, а PKCE привязан к /authorize-коду (которого здесь нет).
    if client.is_public:
        raise AuthorizationError(
            error_code="GRANT_TYPE_NOT_ALLOWED",
            message="client_credentials grant is not available for public clients",
        )

    # Grant-types — раньше Argon2-verify: клиент без `client_credentials` всё
    # равно получит 403, незачем платить за hash и трогать lockout-счётчик
    # (заодно симметрично `issue_authorization_code`).
    if "client_credentials" not in client.grant_types:
        raise AuthorizationError(error_code="GRANT_TYPE_NOT_ALLOWED", message="client_credentials grant not enabled for this client")

    await _verify_client_secret_with_lockout(db, client_repo, client, client_secret)
    if client.failed_secret_attempts:
        await client_repo.reset_failed_attempts(client)
        await db.commit()

    dept_repo = DepartmentRepository(db)
    allowed = await dept_repo.list_active_services(client.department_id)
    effective = [s for s in allowed if s in client.allowed_scopes]

    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    # Payload минимален как у user-JWT: только `sub` + `actor_type` + client-id.
    # department_id/allowed_services/service_roles намеренно НЕ кладём — JWT
    # подписан, но не зашифрован, base64url-decode без ключа раскрыл бы
    # принадлежность отделу и список доступных сервисов. introspect пересчитывает
    # их из БД (`_introspect_oauth_client_jwt`: INTERSECT dept-services с
    # allowed_scopes), поэтому в токене они не нужны и опасны.
    #
    # `sub` для client_credentials JWT — это публичный client_id (`cli_*`), НЕ
    # user_id. introspect диспатчит по `actor_type`: `oauth_client` →
    # revalidate через OAuthClientRepository, не UserRepository (иначе introspect
    # всегда промахивался бы).
    access_token = create_access_token(
        payload={
            "sub": client.client_id,
            "actor_type": "oauth_client",
            "oauth_client_id": client_id,
        },
        expires_delta=ttl,
    )
    audit_service.emit(
        "oauth.client_credentials_token",
        client.id,
        actor_type="service",
        department_id=client.department_id,
        target_id=client_id,
        target_type="oauth_client",
        status="success",
        allowed=True,
        details={
            "client_id": client_id,
            "client_name": client.name,
            "department_id": client.department_id,
            "allowed_scopes": list(client.allowed_scopes),
            "granted_scopes": effective,
            "ttl_seconds": int(ttl.total_seconds()),
        },
        request_id=request_id,
    )
    return OAuthTokenResponse(
        access_token=access_token,
        expires_in=int(ttl.total_seconds()),
        scope=" ".join(effective),
    )


async def refresh_token_grant(
    db: AsyncSession,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    request_id: str | None = None,
) -> OAuthTokenResponse:
    """`grant_type=refresh_token`: ротация OAuth refresh → новый access + refresh.

    Зеркалит user-сессионный `auth_service.refresh`:
    * lookup активного refresh по hash (в БД только hash, raw не хранится);
    * reuse-detection — предъявленный hash в истории ротаций → kill-switch по
      всей цепочке (client_id, user_id), `REFRESH_TOKEN_INVALID`;
    * атомарная CAS-ротация: старый refresh инвалидируется, выдаётся новый;
    * approved scope'ы сохраняются (новый access несёт тот же снапшот);
    * confidential-клиент аутентифицируется client_secret'ом (lockout как у
      code-exchange), public — нет (доказан PKCE на исходном /authorize).
    """
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(client_id)
    if client is None or not client.is_active:
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    if "refresh_token" not in client.grant_types:
        raise AuthorizationError(
            error_code="GRANT_TYPE_NOT_ALLOWED",
            message="refresh_token grant not enabled for this client",
        )

    if not client.is_public:
        await _verify_client_secret_with_lockout(db, client_repo, client, client_secret)
        if client.failed_secret_attempts:
            await client_repo.reset_failed_attempts(client)
            await db.commit()

    refresh_repo = OAuthRefreshTokenRepository(db)
    token_hash = hash_opaque_token(refresh_token)
    stored = await refresh_repo.get_active_by_token_hash(token_hash)

    if stored is None:
        # Нет активной записи под этим hash. Если hash лежит в истории какой-то
        # цепочки — это reuse ротированного refresh: гасим всю цепочку
        # (client_id, user_id). Иначе — просто неизвестный/revoked токен.
        rotated = await refresh_repo.find_rotated_by_old_hash(token_hash)
        if rotated is not None:
            await refresh_repo.mark_suspicious(rotated)
            await refresh_repo.revoke_chain(rotated.client_id, rotated.user_id)
            await db.commit()
            audit_service.emit(
                "oauth.refresh_reuse",
                rotated.user_id,
                target_id=rotated.client_id,
                target_type="oauth_client",
                status="failure",
                allowed=False,
                details={"client_id": rotated.client_id, "token_id": rotated.id},
                request_id=request_id,
            )
        raise AuthenticationError(error_code="REFRESH_TOKEN_INVALID", message="Invalid refresh token")

    # Refresh принадлежит предъявившему клиенту (RFC 6749 §6 — refresh привязан
    # к выдавшему клиенту). Чужой client_id не должен ротировать чужой refresh.
    if stored.client_id != client_id:
        raise AuthenticationError(error_code="REFRESH_TOKEN_INVALID", message="Invalid refresh token")

    if is_expired(stored.expires_at):
        await refresh_repo.revoke(stored)
        await db.commit()
        raise AuthenticationError(error_code="REFRESH_TOKEN_EXPIRED", message="Refresh token expired")

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(stored.user_id)
    if user is None:
        await refresh_repo.revoke(stored)
        await db.commit()
        raise AuthenticationError(error_code="OAUTH_USER_NOT_FOUND", message="User no longer exists")
    if user.status != UserStatus.ACTIVE or not user.is_active:
        await refresh_repo.revoke(stored)
        await db.commit()
        raise AuthenticationError(error_code="OAUTH_USER_INACTIVE", message="User account is not active")

    settings = get_settings()
    new_raw, new_hash = generate_oauth_refresh_token()
    new_refresh_expires = utcnow() + timedelta(days=settings.oauth_refresh_token_ttl_days)
    scopes_snapshot = list(stored.scopes)
    rotated_ok = await refresh_repo.rotate(stored, new_hash, new_refresh_expires)
    if not rotated_ok:
        # CAS-miss: параллельный refresh-обмен уже ротировал эту строку. Benign
        # race (НЕ reuse): победитель получил свежую пару, этот caller ретраит
        # новым refresh'ем. Не зовём kill-switch.
        await db.rollback()
        audit_service.emit(
            "oauth.refresh_race",
            user.id,
            target_id=client_id,
            target_type="oauth_client",
            status="failure",
            allowed=False,
            details={"client_id": client_id, "token_id": stored.id},
            request_id=request_id,
        )
        raise AuthenticationError(
            error_code="REFRESH_TOKEN_RACE",
            message="Refresh token was rotated by a concurrent request; retry with the new token.",
        )

    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    access_token = _build_oauth_access_token(user.id, client_id, scopes_snapshot, ttl)
    await db.commit()

    audit_service.emit(
        "oauth.refresh_token",
        user.id,
        target_id=client_id,
        target_type="oauth_client",
        status="success",
        allowed=True,
        details={
            "client_id": client_id,
            "client_name": client.name,
            "username": user.username,
            "department_id": user.department_id,
            "scopes": scopes_snapshot,
            "ttl_seconds": int(ttl.total_seconds()),
        },
        request_id=request_id,
    )
    return OAuthTokenResponse(
        access_token=access_token,
        expires_in=int(ttl.total_seconds()),
        scope=" ".join(scopes_snapshot),
        refresh_token=new_raw,
    )
