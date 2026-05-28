"""OAuth2: CRUD клиентов + authorization_code flow + client_credentials grant.

Поддерживаем RFC 6749 + RFC 7636 (PKCE). Implicit и hybrid сознательно отказаны
(см. endpoints/oauth2.py для обоснования). PKCE опционален для confidential
клиентов, обязателен для public (SPA/CLI).
"""

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import PlatformRole
from src.core.exceptions import AuthenticationError, AuthorizationError, ConflictError, NotFoundError
from src.core.security import create_access_token, hash_opaque_token
from src.repositories.departments import DepartmentRepository
from src.repositories.oauth_clients import OAuthClientRepository, OAuthCodeRepository
from src.repositories.users import UserRepository
from src.schemas.oauth import (
    OAuthClientCreate,
    OAuthClientCreatedResponse,
    OAuthClientResponse,
    OAuthTokenResponse,
)
from src.services import audit_service
from src.utils.time import is_expired, utcnow

_SECRET_PREFIX_LEN = 12

# RFC 7636 §4.3: code_challenge_method ∈ {"S256", "plain"}. "plain" разрешён
# спецификацией, но S256 обязателен для public client'ов — мы принимаем оба
# на стороне сервера (валидируем method), решение оставляем за клиентом.
_PKCE_METHODS = {"S256", "plain"}


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
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != data.department_id:
            raise AuthorizationError(
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
        created_by=actor_id,
    )
    await db.commit()
    # raw_secret уходит в details — sanitizer заменит на <SECRET> по ключу client_secret
    audit_service.emit(
        "oauth_client.create", actor_id, target_id=client.id, target_type="oauth_client",
        request_id=request_id,
        details={
            "name": data.name,
            "department_id": data.department_id,
            "department_name": dept.display_name,
            "redirect_uris": list(data.redirect_uris),
            "allowed_scopes": list(data.allowed_scopes),
            "grant_types": list(data.grant_types),
            "client_secret_prefix": secret_prefix,
            "client_secret": raw_secret,
            "description": data.description,
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
        actor = await user_repo.get_by_id(actor_id)
        dept_id = actor.department_id if actor else None
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
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != client.department_id:
            raise AuthorizationError(
                error_code="DEPARTMENT_ACCESS_DENIED",
                message="Cannot delete OAuth2 client outside your department",
            )

    await client_repo.deactivate(client)
    await db.commit()
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

    # PKCE-валидация (если клиент прислал challenge — method обязателен и из
    # дозволенного множества; default по RFC 7636 §4.3 — "plain", но мы
    # предпочитаем явный method, чтобы не было сюрпризов с downgrade).
    pkce_challenge: str | None = None
    pkce_method: str | None = None
    if code_challenge:
        method = code_challenge_method or "plain"
        if method not in _PKCE_METHODS:
            raise AuthorizationError(
                error_code="PKCE_METHOD_INVALID",
                message=f"code_challenge_method must be one of {sorted(_PKCE_METHODS)}",
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

    if not hmac.compare_digest(hash_opaque_token(client_secret), client.client_secret_hash):
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    code_hash = hash_opaque_token(code)
    code_repo = OAuthCodeRepository(db)
    auth_code = await code_repo.get_by_hash(code_hash)
    if auth_code is None:
        raise AuthenticationError(error_code="OAUTH_CODE_INVALID", message="Authorization code is invalid or already used")

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
    # and single-use"). Идёт ПОСЛЕ всех валидаций (PKCE/redirect/expiry),
    # чтобы legit-клиент с битым verifier'ом не сжёг свой код, и ДО работы по
    # выписке JWT (role/dept lookup), иначе при гонке winner+loser потратят
    # CPU зря. Детали — в `OAuthCodeRepository.mark_used`.
    if not await code_repo.mark_used(auth_code):
        # Кто-то уже consume'нул этот код параллельно (другой /token-запрос
        # с тем же `code` пришёл первым). Не reuse-attack в строгом смысле —
        # OAuth-replay: атакующий пытается обменять перехваченный код, пока
        # legit-client тоже обменивает. RFC 6749 §5.2 → `invalid_grant`.
        raise AuthenticationError(
            error_code="INVALID_GRANT",
            message="Authorization code is invalid or already used",
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(auth_code.user_id)
    if user is None:
        raise AuthenticationError(error_code="OAUTH_USER_NOT_FOUND", message="User no longer exists")

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
    access_token = create_access_token(
        payload={
            "sub": user.id,
            "actor_type": "user",
            "oauth_client_id": client_id,
            "oauth_scopes": oauth_scopes,
        },
        expires_delta=ttl,
    )
    await db.commit()
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
        },
        request_id=request_id,
    )
    return OAuthTokenResponse(
        access_token=access_token,
        expires_in=int(ttl.total_seconds()),
        scope=" ".join(auth_code.scopes),
    )


async def client_credentials_token(
    db: AsyncSession,
    client_id: str,
    client_secret: str,
    request_id: str | None = None,
) -> OAuthTokenResponse:
    """Выдать access_token по client_credentials grant (machine-to-machine, без user_id).

    No state mutated: функция чисто read — клиентских counter'ов вроде
    `last_token_issued_at` или per-request тротлинга в `OAuthClient` нет,
    поэтому `db.commit()` не нужен (в отличие от `exchange_code`). Если в
    будущем добавится usage-tracking — обязательно положить `await db.commit()`
    перед `return`, иначе update уйдёт в rollback через `get_db()`.
    """
    client_repo = OAuthClientRepository(db)
    client = await client_repo.get_by_client_id(client_id)
    if client is None or not client.is_active:
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    if not hmac.compare_digest(hash_opaque_token(client_secret), client.client_secret_hash):
        raise AuthenticationError(error_code="OAUTH_CLIENT_INVALID", message="Invalid client credentials")

    if "client_credentials" not in client.grant_types:
        raise AuthorizationError(error_code="GRANT_TYPE_NOT_ALLOWED", message="client_credentials grant not enabled for this client")

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
