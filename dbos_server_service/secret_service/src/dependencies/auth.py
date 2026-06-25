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
from collections import OrderedDict
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
    "require_transfer_recover_context",
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
# на auth_service.
#
# `_INTROSPECT_CACHE_MAXSIZE` — жёсткая верхняя граница числа entries в кэше.
# До неё кэш рос как обычный `dict` без вытеснения: при большом числе уникальных
# токенов (PAT/bot per request, burst short-lived JWT) объём в памяти прыгал
# вверх и никогда не освобождался до рестарта pod'а. OrderedDict + LRU-вытеснение
# при превышении maxsize удерживает работающий объём в постоянной памяти.
# При TTL=5s и нормальном RPS realtime-окно содержит RPS×5 уникальных токенов;
# 1024 покрывает burst до ~200 RPS уникальных токенов, что больше любого
# текущего сценария.
_INTROSPECT_TTL_SECONDS = 5.0
_INTROSPECT_CACHE_MAXSIZE = 1024
_introspect_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()


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
    # Cache-hit — двигаем запись в хвост, чтобы LRU-eviction не выкидывал
    # активно используемый токен.
    _introspect_cache.move_to_end(key)
    return body


def _cache_put(key: str, body: dict) -> None:
    _introspect_cache[key] = (time.monotonic() + _INTROSPECT_TTL_SECONDS, body)
    _introspect_cache.move_to_end(key)
    # Bounded size — LRU-вытеснение. `popitem(last=False)` снимает самый
    # старый по последнему обращению; в норме это уже expired entry, в
    # burst'е — самый «холодный» token, обновится при следующем introspect'е.
    while len(_introspect_cache) > _INTROSPECT_CACHE_MAXSIZE:
        _introspect_cache.popitem(last=False)


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
    # X-Service-Identity для per-service режима в auth_service'е (SERVICE_API_KEYS
    # под идентичностью secret_service содержит наш introspect_service_api_key).
    headers = {
        **bearer_header(settings.introspect_service_api_key),
        "X-Service-Identity": "secret_service",
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
    """Маппинг introspect-ответа в Identity.

    Пустой `sub` отбиваем: owner-check'и сравнивают `cred.owner_user_id ==
    identity.user_id`, и пустая строка совпала бы с любой кред'ой без
    owner'а. Лучше 401, чем тихий identity с user_id="".
    """
    sub = body.get("sub")
    if not sub:
        raise AuthenticationError(
            error_code="INVALID_TOKEN",
            message="introspect response is missing subject (sub)",
        )
    return Identity(
        user_id=sub,
        username=body.get("username") or "",
        # introspect отдаёт `subject_type` ("user"/"bot"/"oauth_client").
        # PAT auth_service маппит на subject_type=`user`, отдельного значения нет.
        actor_type=body.get("subject_type") or "user",
        department_id=body.get("department_id"),
        department_name=body.get("department_name"),
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
        department_name=identity.department_name,
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


def require_transfer_recover_context(identity: Identity) -> Identity:
    """Endpoint-guard для `/transfer` и `/recover` — два разных пути входа.

    Штатный путь: пользователь/бот отдела с доступом к secret_service. Гейт на
    конкретную роль (admin владеющего dep'а) живёт ниже, в
    `credential_service` — здесь только отсекаем чужой сервис и oauth-client'ов
    тем же `require_user_context`.

    Emergency-путь: `account_admin`. У платформенного админа `department_id=null`
    и нет department-service-access, поэтому обычный `require_user_context` его
    отбил бы 403'кой. Это единственная точка, где account_admin пропускается во
    внутрь credentials-эндпоинтов — нужна для восстановления кред'ы, чей
    владеющий отдел удалён (живого service-admin'а у такого отдела нет). Узость
    важна: account_admin проходит ТОЛЬКО transfer/recover, обычный CRUD/reveal
    остаётся за `require_user_context` и для него закрыт.
    """
    if identity.platform_role == "account_admin":
        return identity
    return require_user_context(identity)


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
    """Носитель `admin`-роли `secret_service` (per-(dept, service)).

    Этот guard не привязан к конкретному dept'у — он только отсекает не-админов
    на endpoint-level. Per-dept-binding (admin dep_A не лезет в cred'ы dep_B)
    enforce'ится в business-логике через `access_service._is_service_admin_for`
    и `credential_service._is_service_admin_for`.
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
