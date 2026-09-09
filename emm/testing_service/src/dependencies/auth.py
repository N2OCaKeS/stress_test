"""Зависимости authentication/authorization для testing_service.

Сервис НЕ верифицирует JWT локально — каждый Authorization идёт через
`POST {AUTH_SERVICE_URL}/api/auth/v1/authorization/introspect`. Это даёт:

* role/department revocations подхватываются мгновенно;
* PAT и bot-токены принимаются наравне с user JWT — auth_service единственный
  источник истины «что считается валидным bearer'ом».

Паттерн скопирован с `server_service/src/dependencies/auth.py` (самая свежая
версия после этой сессии) — пул `httpx.AsyncClient`, dedup через
`request.state.introspect_body` (заполняется будущим `platform_admin_guard`-
подобным middleware, если он появится; пока просто fallback на прямой
introspect на каждый запрос) и shape-precheck токена до HTTP-roundtrip'а.
Из server_service взяты только три сущности, которые нужны каркасу:
`CurrentIdentity`, `CurrentUserIdentity`, `require_internal_caller` —
остальные специфичные для server_service guard'ы (permission-matrix,
account_admin-only и т.п.) сюда не переносились, появятся по мере надобности.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Callable

import httpx
from fastapi import Depends, Header, Request

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
    "AuthenticatedIdentity",
    "BearerToken",
    "CurrentIdentity",
    "CurrentUserIdentity",
    "Identity",
    "SERVICE_NAME",
    "get_authenticated_identity",
    "get_bearer_token",
    "get_current_identity",
    "require_internal_caller",
    "require_user_context",
]

_INTROSPECT_PATH = "/api/auth/v1/authorization/introspect"

# Минимальная длина токена для shape-precheck. JWT редко короче ~100 символов,
# `dbos_pat_…`/`dbos_bot_…` — минимум 9 (префикс) + энтропийный хвост.
_MIN_TOKEN_LENGTH = 20
_VALID_TOKEN_PREFIXES = ("eyJ", "dbos_pat_", "dbos_bot_")

# Module-level pooled client. Поднимается в FastAPI lifespan (main.py) startup,
# закрывается на shutdown'е.
_introspect_client: httpx.AsyncClient | None = None


class Identity:
    """Identity-context, который видит каждый endpoint.

    Обычный класс, не pydantic — структура строится из introspect-ответа,
    валидация не нужна, pydantic-overhead на hot-path ни к чему.
    """

    __slots__ = (
        "user_id",
        "username",
        "actor_type",
        "department_id",
        "department_name",
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
        department_name: str | None = None,
        allowed_services: list[str],
        service_roles: dict[str, list[str]],
        is_banned: bool,
        platform_role: str | None,
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.actor_type = actor_type
        self.department_id = department_id
        self.department_name = department_name
        self.allowed_services = allowed_services
        self.service_roles = service_roles
        self.is_banned = is_banned
        self.platform_role = platform_role

    def roles_for(self, service: str) -> list[str]:
        """Удобный shortcut на service_roles.get(name, [])."""
        return self.service_roles.get(service, [])


def _is_token_shape_valid(token: str) -> bool:
    """Дёшево фильтруем bearer-мусор ДО HTTP-вызова в auth_service."""
    if not token or len(token) < _MIN_TOKEN_LENGTH:
        return False
    return token.startswith(_VALID_TOKEN_PREFIXES)


def _extract_bearer(request: Request) -> str | None:
    """Достать сырое значение токена после префикса 'Bearer '."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


async def _introspect(token: str) -> dict:
    """Позвать introspect-endpoint auth_service. Сетевые ошибки → 503."""
    if not _is_token_shape_valid(token):
        raise AuthenticationError(
            error_code="INVALID_TOKEN_FORMAT",
            message="Bearer token has invalid format",
        )

    settings = get_settings()
    headers = {
        **bearer_header(settings.introspect_service_api_key),
        "X-Service-Identity": "testing_service",
    }

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
            error_code="AUTH_SERVICE_TIMEOUT",
            message="auth_service did not respond in time",
        ) from exc
    except httpx.ConnectError as exc:
        raise ServiceUnavailableError(
            error_code="AUTH_SERVICE_UNREACHABLE",
            message="Unable to connect to auth_service",
        ) from exc
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(
            error_code="AUTH_SERVICE_ERROR",
            message=f"Unexpected error talking to auth_service: {type(exc).__name__}",
        ) from exc

    if response.status_code != 200:
        raise ServiceUnavailableError(
            error_code="AUTH_SERVICE_ERROR",
            message=f"auth_service returned {response.status_code}",
        )
    return response.json()


def _to_identity(body: dict) -> Identity:
    """Маппинг introspect-ответа в Identity."""
    sub = body.get("sub")
    if not sub:
        raise AuthenticationError(
            error_code="INVALID_TOKEN",
            message="introspect response is missing subject (sub)",
        )
    return Identity(
        user_id=sub,
        username=body.get("username") or "",
        actor_type=body.get("subject_type") or "user",
        department_id=body.get("department_id"),
        department_name=body.get("department_name"),
        allowed_services=body.get("allowed_services", []),
        service_roles=body.get("service_roles", {}),
        is_banned=body.get("is_banned", False),
        platform_role=body.get("platform_role"),
    )


async def get_current_identity(request: Request) -> Identity:
    """Resolve identity вызывающего через introspect в auth_service.

    Инварианты, проверяемые ДО того, как endpoint увидит запрос:
      1. Bearer есть и introspect вернул `active=true`;
      2. user не забанен;
      3. department имеет доступ к `testing_service`.
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_MISSING",
            message="Missing bearer token",
        )

    # Дедупликация per-request: если когда-нибудь появится middleware,
    # делающий свой introspect и кладущий body в request.state (по образцу
    # server_service's platform_admin_guard) — переиспользуем его. Сейчас
    # такого middleware нет, поэтому это всегда fallback на прямой вызов.
    body = getattr(request.state, "introspect_body", None)
    if body is None:
        body = await _introspect(token)
    if not body.get("active"):
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_INVALID",
            message="Token is invalid, expired or revoked",
        )

    identity = _to_identity(body)
    if identity.is_banned:
        raise AuthenticationError(
            error_code="USER_BANNED",
            message="User is banned",
        )
    if SERVICE_NAME not in identity.allowed_services:
        raise AuthorizationError(
            error_code="SERVICE_ACCESS_DENIED",
            message=f"User's department has no access to {SERVICE_NAME}",
        )
    audit_context.update_context(
        actor_id=identity.user_id,
        username=identity.username,
        department_id=identity.department_id,
        department_name=identity.department_name,
        subject_type=identity.actor_type,
    )
    return identity


CurrentIdentity = Annotated[Identity, Depends(get_current_identity)]


async def get_authenticated_identity(request: Request) -> Identity:
    """Resolve identity без проверки доступа департамента к testing_service.

    Облегчённый вариант `get_current_identity` под платформенные каталоги
    (глобальные переменные): читать их может любой аутентифицированный актор,
    включая платформенные роли, у которых нет ни `department_id`, ни
    testing_service в `allowed_services`. Поэтому `SERVICE_ACCESS_DENIED`-гейт
    здесь намеренно не проверяется.

    Проверяется только: bearer есть, introspect вернул `active=true`, актор
    не забанен. Анонимный запрос отбивается 401 — каталог открыт всем
    аутентифицированным, но не анонимам. Запись в каталог по-прежнему идёт
    через `CurrentUserIdentity` + матрицу прав.
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_MISSING",
            message="Missing bearer token",
        )
    body = getattr(request.state, "introspect_body", None)
    if body is None:
        body = await _introspect(token)
    if not body.get("active"):
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_INVALID",
            message="Token is invalid, expired or revoked",
        )
    identity = _to_identity(body)
    if identity.is_banned:
        raise AuthenticationError(
            error_code="USER_BANNED",
            message="User is banned",
        )
    audit_context.update_context(
        actor_id=identity.user_id,
        username=identity.username,
        department_id=identity.department_id,
        department_name=identity.department_name,
        subject_type=identity.actor_type,
    )
    return identity


AuthenticatedIdentity = Annotated[Identity, Depends(get_authenticated_identity)]


def require_user_context(
    identity: Annotated[Identity, Depends(get_current_identity)],
) -> Identity:
    """Отбивает m2m identity (OAuth `client_credentials`) на user-facing endpoint'ах.

    testing_service оперирует пользовательскими и per-department данными —
    у `oauth_client` нет ни department_id, ни места в матрице прав.
    """
    if identity.actor_type == "oauth_client":
        raise AuthorizationError(
            error_code="USER_CONTEXT_REQUIRED",
            message=(
                "This endpoint requires user context; "
                "OAuth client_credentials tokens are not accepted"
            ),
        )
    return identity


CurrentUserIdentity = Annotated[Identity, Depends(require_user_context)]


def get_bearer_token(request: Request) -> str:
    """Достаёт сырой bearer из заголовка текущего запроса, без введения через introspect.

    Нужен, когда исходящий вызов должен пробросить ТОТ ЖЕ токен, которым
    пришёл запрос, а не сервисный секрет — pass-through к server_service
    (`server_client.get_server`), где видимость зависит от department_id
    держателя токена, а не от того, что testing_service вообще аутентифицирован.
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_MISSING",
            message="Missing bearer token",
        )
    return token


BearerToken = Annotated[str, Depends(get_bearer_token)]


# ── Service-to-service (internal endpoints) ─────────────────────────────────


def require_internal_caller(
    *allowed_identities: str,
) -> Callable[[Request, str | None], str]:
    """Dependency factory под internal-эндпоинты с shared-secret авторизацией.

    Возвращает FastAPI-зависимость, которая:

    1. Читает `X-Service-Identity` header (без него → 401 SERVICE_IDENTITY_REQUIRED).
    2. Проверяет, что identity входит в whitelist `allowed_identities` для роута
       (вне списка → 403 SERVICE_IDENTITY_NOT_ALLOWED).
    3. Достаёт ожидаемый ключ из `settings.service_api_keys[identity]`.
    4. Сравнивает `Authorization: Bearer <token>` constant-time'ом.

    Возвращает identity (str) — endpoint может писать его в audit/details.
    Не ходит в auth_service introspect — это отдельный s2s-канал, без user
    identity/department (например, server_service → testing_service callback
    из §5.1 плана миграции, волна 2/5).
    """
    if not allowed_identities:
        raise ValueError("require_internal_caller: allowed_identities must be non-empty")
    allowed = frozenset(allowed_identities)

    async def _check(
        request: Request,
        x_service_identity: str | None = Header(
            default=None,
            alias="X-Service-Identity",
            description="Идентичность вызывающего сервиса (например, `server_service`).",
        ),
    ) -> str:
        if not x_service_identity:
            raise AuthenticationError(
                error_code="SERVICE_IDENTITY_REQUIRED",
                message="X-Service-Identity header is required for this endpoint",
            )
        identity = x_service_identity.strip()
        if identity not in allowed:
            raise AuthorizationError(
                error_code="SERVICE_IDENTITY_NOT_ALLOWED",
                message=f"Service identity {identity!r} is not allowed to call this endpoint",
            )
        token = _extract_bearer(request)
        if not token:
            raise AuthenticationError(
                error_code="INVALID_SERVICE_TOKEN",
                message="Missing or malformed bearer token for service identity",
            )
        expected = get_settings().service_api_keys.get(identity)
        if not expected:
            raise AuthenticationError(
                error_code="INVALID_SERVICE_TOKEN",
                message="Service token is invalid",
            )
        if not hmac.compare_digest(token, expected):
            raise AuthenticationError(
                error_code="INVALID_SERVICE_TOKEN",
                message="Service token is invalid",
            )
        audit_context.update_context(
            actor_id=f"ops:{identity}",
            username=identity,
            subject_type="service",
        )
        return identity

    return _check
