"""Зависимости authentication/authorization для secret_service.

Сервис НЕ верифицирует JWT локально — каждый Authorization идёт через
`POST {AUTH_SERVICE_URL}/api/auth/v1/authorization/introspect`. Это даёт:

* role/department revocations подхватываются мгновенно;
* PAT и bot-токены принимаются наравне с user JWT — auth_service единственный
  источник истины «что считается валидным bearer'ом».

### Identity-кэш на 5 секунд

В отличие от server_service (где middleware дедуплицирует introspect внутри
одного запроса), secret_service — горячий по reveal'ам: UI может опрашивать
карточку секрета каждые пару секунд. Без кэша это полный introspect-roundtrip
на каждый клик. Кэшируем результат на ~5s (короткое окно — отозванный токен
перестаёт работать в течение этих 5s, что приемлемо). Ключ — SHA256(token);
plaintext-токен в memory-структуре не светим.

### Connection pool

`httpx.AsyncClient` живёт module-level (`_introspect_client`), управляется
FastAPI lifespan'ом. Outside lifecycle — fall back to per-call client.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Annotated

import httpx
from fastapi import Depends, Request

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME
from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ServiceUnavailableError,
)
from src.core.http import bearer_header
from src.services import audit_context

logger = logging.getLogger(__name__)

__all__ = [
    "CurrentIdentity",
    "Identity",
    "SERVICE_NAME",
    "get_identity",
    "require_account_admin",
    "require_dept_admin_for",
    "require_service_admin",
    "require_user_context",
]

_INTROSPECT_PATH = "/api/auth/v1/authorization/introspect"

# Минимальная длина токена для shape-precheck. JWT обычно > ~100 символов,
# `dbos_pat_…`/`dbos_bot_…` — минимум 9 (префикс) + энтропийный хвост.
_MIN_TOKEN_LENGTH = 20

_VALID_TOKEN_PREFIXES = ("eyJ", "dbos_pat_", "dbos_bot_")

# Module-level pooled client. Поднимается в FastAPI lifespan startup.
_introspect_client: httpx.AsyncClient | None = None


# ── Identity model ───────────────────────────────────────────────────────────


class Identity:
    """Identity-context, который видит каждый endpoint.

    Использую обычный класс (не pydantic) — модель чисто внутренняя, валидация
    не нужна (структуру строим сами из introspect-ответа), а pydantic-overhead
    на hot-path reveal'ов ни к чему.
    """

    __slots__ = (
        "user_id",
        "username",
        "actor_type",
        "department_id",
        "allowed_services",
        "service_roles",
        "is_banned",
        "platform_role",
    )

    def __init__(
        self,
        *,
        user_id: str,
        username: str,
        actor_type: str,
        department_id: str | None,
        allowed_services: list[str],
        service_roles: dict[str, list[str]],
        is_banned: bool,
        platform_role: str | None,
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.actor_type = actor_type
        self.department_id = department_id
        self.allowed_services = allowed_services
        self.service_roles = service_roles
        self.is_banned = is_banned
        self.platform_role = platform_role

    def roles_for(self, service: str) -> list[str]:
        """Удобный shortcut на service_roles.get(name, [])."""
        return self.service_roles.get(service, [])


# ── Token helpers ────────────────────────────────────────────────────────────


def _is_token_shape_valid(token: str) -> bool:
    """Дёшево фильтруем bearer-мусор до HTTP-вызова в auth_service."""
    if not token or len(token) < _MIN_TOKEN_LENGTH:
        return False
    return token.startswith(_VALID_TOKEN_PREFIXES)


def _extract_bearer(request: Request) -> str | None:
    """Достать сырое значение токена после префикса 'Bearer '."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _hash_token(token: str) -> str:
    """SHA256(token) — ключ для in-memory кэша. Plaintext не светим."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── Introspect cache ─────────────────────────────────────────────────────────

# TTL в секундах. Короткое окно: отозванный токен перестаёт работать в течение
# 5s, что приемлемо для UI-polling'а и не даёт slowloris-burst'у амплифицировать
# на auth_service. dict ключ → (expires_at, body).
_INTROSPECT_TTL_SECONDS = 5.0
_introspect_cache: dict[str, tuple[float, dict]] = {}


def _cache_get(key: str) -> dict | None:
    entry = _introspect_cache.get(key)
    if entry is None:
        return None
    expires_at, body = entry
    if time.monotonic() >= expires_at:
        # Истёк — лениво подметаем эту конкретную запись. Полного sweep'а
        # cache'а не делаем — он маленький (per-process, время жизни 5s),
        # дешевле подметать на miss'е, чем держать тикающий sweep-таск.
        _introspect_cache.pop(key, None)
        return None
    return body


def _cache_put(key: str, body: dict) -> None:
    _introspect_cache[key] = (time.monotonic() + _INTROSPECT_TTL_SECONDS, body)


def _cache_clear_for_tests() -> None:
    """Test helper — сбросить кэш между прогонами."""
    _introspect_cache.clear()


# ── Introspect call ──────────────────────────────────────────────────────────


async def _introspect(token: str) -> dict:
    """Позвать introspect-endpoint auth_service. Сетевые ошибки → 503.

    Использует pooled `_introspect_client`. Outside the app lifecycle —
    fall back to per-call client. Bearer-shape pre-check здесь, ДО любого
    HTTP-roundtrip'а — отсекает trash-токены без нагрузки на auth_service.
    """
    if not _is_token_shape_valid(token):
        raise AuthenticationError(
            error_code="INVALID_TOKEN_FORMAT",
            message="Bearer token has invalid format",
        )

    settings = get_settings()
    headers = bearer_header(settings.introspect_service_api_key)

    client = _introspect_client
    try:
        if client is not None:
            response = await client.post(
                _INTROSPECT_PATH, json={"token": token}, headers=headers
            )
        else:
            logger.debug("introspect: pooled client missing, using per-call fallback")
            url = f"{settings.auth_service_url.rstrip('/')}{_INTROSPECT_PATH}"
            async with httpx.AsyncClient(timeout=settings.auth_request_timeout_seconds) as fallback:
                response = await fallback.post(url, json={"token": token}, headers=headers)
    except httpx.TimeoutException as exc:
        raise ServiceUnavailableError(
            error_code="AUTH_SERVICE_UNAVAILABLE",
            message="auth_service did not respond in time",
        ) from exc
    except httpx.ConnectError as exc:
        raise ServiceUnavailableError(
            error_code="AUTH_SERVICE_UNAVAILABLE",
            message="Unable to connect to auth_service",
        ) from exc
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(
            error_code="AUTH_SERVICE_UNAVAILABLE",
            message=f"Unexpected error talking to auth_service: {type(exc).__name__}",
        ) from exc

    if response.status_code != 200:
        raise ServiceUnavailableError(
            error_code="AUTH_SERVICE_UNAVAILABLE",
            message=f"auth_service returned {response.status_code}",
        )
    return response.json()


def _to_identity(body: dict) -> Identity:
    """Маппинг introspect-ответа в Identity."""
    return Identity(
        user_id=body.get("sub") or "",
        username=body.get("username") or "",
        # introspect отдаёт `subject_type` ("user"/"bot"/"oauth_client").
        # PAT auth_service маппит на subject_type=`user`, отдельного значения нет.
        actor_type=body.get("subject_type") or "user",
        department_id=body.get("department_id"),
        allowed_services=body.get("allowed_services", []),
        service_roles=body.get("service_roles", {}),
        is_banned=body.get("is_banned", False),
        platform_role=body.get("platform_role"),
    )


# ── Main dependency ──────────────────────────────────────────────────────────


async def get_identity(request: Request) -> Identity:
    """Resolve identity вызывающего через introspect в auth_service.

    Инварианты, проверяемые до того, как endpoint увидит запрос:
      1. Bearer есть, и introspect вернул `active=true`;
      2. user не забанен.

    Проверка `secret_service in allowed_services` НЕ делается здесь — это
    endpoint-level guard (см. `require_user_context` / endpoint-specific
    decorator'ы). Сделано так, чтобы platform-admin-only ручки (`/recover`,
    `/transfer`) могли работать без department-service-access у самого
    account_admin'а.
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_MISSING",
            message="Missing bearer token",
        )

    cache_key = _hash_token(token)
    body = _cache_get(cache_key)
    if body is None:
        body = await _introspect(token)
        # Кэшируем даже active=False, чтобы повторный спам мёртвым токеном
        # не амплифицировался на auth_service в течение TTL-окна.
        _cache_put(cache_key, body)

    if not body.get("active"):
        raise AuthenticationError(
            error_code="UNAUTHORIZED",
            message="Token is invalid, expired or revoked",
        )

    identity = _to_identity(body)
    if identity.is_banned:
        raise AuthenticationError(
            error_code="UNAUTHORIZED",
            message="User is banned",
        )

    audit_context.update_context(
        actor_id=identity.user_id,
        username=identity.username,
        department_id=identity.department_id,
        subject_type=identity.actor_type,
    )
    return identity


CurrentIdentity = Annotated[Identity, Depends(get_identity)]


# ── Guard helpers ────────────────────────────────────────────────────────────


def require_user_context(identity: Identity) -> Identity:
    """OAuth-client'ам нет места в secret_service: они technical-only.

    secret_service оперирует пользовательскими и department-кредами; у oauth-client
    нет ни личных кред, ни принадлежности к dep'у. Отбиваем 403.
    Заодно здесь же проверяем `secret_service in allowed_services` —
    запросы от dep'а без access'а к нашему сервису отвергаем.
    """
    if identity.actor_type == "oauth_client":
        raise AuthorizationError(
            error_code="SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT",
            message="oauth_client tokens are not accepted by secret_service",
        )
    if SERVICE_NAME not in identity.allowed_services:
        raise AuthorizationError(
            error_code="SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT",
            message=f"Department has no access to {SERVICE_NAME}",
        )
    return identity


def require_account_admin(identity: Identity) -> Identity:
    """`account_admin` — платформенный админ; нужен для transfer ownership
    при удалении владеющего dep'а в cross_department-кредах."""
    if identity.platform_role != "account_admin":
        raise AuthorizationError(
            error_code="ACCOUNT_ADMIN_REQUIRED",
            message="account_admin role required",
        )
    return identity


def require_service_admin(identity: Identity) -> Identity:
    """`service_admin` — носитель `admin` роли в `secret_service`.

    Может: читать (без reveal) любую креду, удалять с обязательным reason,
    transfer ownership в ограниченных случаях.
    """
    if "admin" not in identity.roles_for(SERVICE_NAME):
        raise AuthorizationError(
            error_code="SERVICE_ADMIN_REQUIRED",
            message=f"admin role in {SERVICE_NAME} required",
        )
    return identity


def require_dept_admin_for(identity: Identity, dept_id: str) -> Identity:
    """`department_admin` своего dep'а. account_admin тоже пускаем — он stronger."""
    if identity.platform_role == "account_admin":
        return identity
    if (
        identity.platform_role == "department_admin"
        and identity.department_id == dept_id
    ):
        return identity
    raise AuthorizationError(
        error_code="DEPT_ADMIN_REQUIRED",
        message=f"department_admin of {dept_id} required",
        details={"required_dept_id": dept_id},
    )
