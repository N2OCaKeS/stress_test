"""Зависимости authentication/authorization.

Сервис НЕ верифицирует JWT локально — каждый запрос идёт через
`POST {AUTH_SERVICE_URL}/api/auth/v1/authorization/introspect`. Платим один
лишний HTTP-roundtrip per request, взамен получаем:

* role/department revocations подхватываются мгновенно;
* PAT и bot-токены принимаются наравне с user JWT (auth_service —
  единственный источник истины «что считается валидным bearer'ом»).

### Connection pool

`httpx.AsyncClient` — **module-level** (`_introspect_client`), управляется
FastAPI `lifespan` в `src/main.py`:

* startup → создаётся один `AsyncClient` с `base_url=auth_service_url`,
  shared timeout и bounded pool limits (`max_connections=20`,
  `max_keepalive_connections=10`);
* shutdown → `await _introspect_client.aclose()`.

Почему это важно: каждый Authorization-set запрос идёт в `_introspect()`,
так что slowloris-burst с trash-токенами без пула открывал бы свежий
TCP+TLS handshake на каждый запрос и истощил FD-пул с обеих сторон.
Pooled client ставит потолок на исходящие соединения и амортизирует
TLS-handshake между запросами.

### TTL-кэш introspect

`platform_admin_guard` middleware и endpoint dependency
`get_current_identity` оба зовут `_introspect(token)` per request — это
+1 outbound roundtrip per non-public path (double introspect). На 500 RPS
без кэша = 1000 introspect/sec в auth_service.

Module-level TTL-кэш ``_identity_cache: dict[token_hash, (body, expires_at)]``
с TTL=5 секунд (короткий, чтобы ban/revoke подхватывались за ~5s, при
рекомендуемых ~30s revalidate'ах из external docs всё ещё в SLA).
Cache key — ``sha256(token).hexdigest()`` (не сам токен, чтобы plaintext-
bearer не лежал в памяти процесса). Кэшируем **только** валидные
positive-ответы (``body.get("active")`` истинно) — отрицательные не имеет
смысла кэшировать, ban'нутый/expired токен 401 отбивается быстрее повторным
introspect'ом (или может попасть в hot-reset через retry клиента).

Оба caller'а (middleware + endpoint dep) идут через
``_get_or_cache_introspect(token)`` → cache hit ровно один introspect
на токен в окне 5s. После expiry — fresh introspect, новые roles/ban'ы
подхватываются.

Кэш — per-process, in-memory; multi-worker uvicorn → каждый процесс свой.
В тестах ``conftest._patch_introspect`` подменяет ``_introspect`` на
fake (ниже кэш-слоя), поэтому существующие тесты ничего не знают про
кэш — он включён только для production-пути через настоящий httpx-вызов.
"""

import hashlib
import time
from typing import Annotated

import httpx
from fastapi import Depends, Request

from src.core.config import get_settings
from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ServiceUnavailableError,
)
from src.schemas.identity import IdentityContext
from src.services import audit_context

SERVICE_NAME = "server_service"

_INTROSPECT_PATH = "/api/auth/v1/authorization/introspect"

# Минимальная длина токена для shape-precheck. JWT редко короче ~100 символов,
# `dbos_pat_…`/`dbos_bot_…` — минимум 9 (префикс) + энтропийный хвост. 20 —
# заведомо безопасный нижний бар, отсекающий мусор типа "abc", "12345", "test".
_MIN_TOKEN_LENGTH = 20

# Префиксы реальных bearer-токенов, которые выдаёт auth_service:
# * `eyJ`       — JWT (base64-кодированный header `{"alg":...}` всегда начинается с `eyJ`)
# * `dbos_pat_` — Personal Access Token
# * `dbos_bot_` — токен bot-аккаунта
_VALID_TOKEN_PREFIXES = ("eyJ", "dbos_pat_", "dbos_bot_")

# TTL кэша introspect (секунды). 5 — компромисс: ban/revoke подхватываются
# за ~5s (рекомендуемые external SLAs — 30s revalidate, мы укладываемся),
# а под нагрузкой 500 RPS на один токен — 1 introspect / 5 сек вместо 1000/сек.
_INTROSPECT_CACHE_TTL_SECONDS = 5.0

# Module-level кэш: token-hash → (body_dict, expires_at_monotonic).
# Хэшируем SHA-256 потому что:
#   * не хотим держать plaintext bearer в памяти процесса (heap dump / coredump
#     при сбое — токены становятся доступны при post-mortem);
#   * key должен быть стабильно сравним и достаточно широк, чтобы избежать
#     коллизий между разными PAT'ами.
# `monotonic()` — устойчиво к NTP-skew (не падаем при отрицательных дельтах).
_identity_cache: dict[str, tuple[dict, float]] = {}


def _is_token_shape_valid(token: str) -> bool:
    """Дёшево фильтруем bearer-мусор ДО HTTP-вызова в auth_service.

    Slowloris-mitigation (c): trash-токены вроде `aaaaa`, `123`, `test` идут
    в auth_service зря — bandwidth + load. Реальный токен auth_service всегда
    либо JWT (`eyJ…`), либо PAT (`dbos_pat_…`), либо bot (`dbos_bot_…`), и
    длиной заведомо >= 20. Всё остальное — 401 без roundtrip'а.
    """
    if not token or len(token) < _MIN_TOKEN_LENGTH:
        return False
    return token.startswith(_VALID_TOKEN_PREFIXES)


def _token_cache_key(token: str) -> str:
    """SHA-256 hex digest токена — стабильный key для `_identity_cache`."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clear_introspect_cache() -> None:
    """Сбросить TTL-кэш. Используется в тестах + при shutdown lifespan'а.

    Production-код кэш не дёргает напрямую — `_get_or_cache_introspect`
    сам управляет lifecycle'ом записей через expiry.
    """
    _identity_cache.clear()


async def _get_or_cache_introspect(token: str) -> dict:
    """Cache-aware wrapper над `_introspect`.

    Алгоритм:

    1. Проверить кэш по ``sha256(token)``. Если запись есть и не expired —
       вернуть body из кэша.
    2. Иначе вызвать ``_introspect(token)`` (с polling-pool'ом + bearer-shape
       pre-check'ом + auth_service-roundtrip).
    3. Кэшировать **только positive** результаты (``body["active"]``
       истинно). Negative результаты (``active=False``) не кэшируем —
       это позволяет hot-revoke restart'нуть кэш через invalidation на
       стороне auth_service (token регенерируется → новый hash, старый
       lazy-evict'нется по TTL).
    4. Expiry — `monotonic() + TTL`. Stale-записи удаляются лениво при
       следующем lookup'е (без background eviction'а — кэш на несколько
       тысяч tokens умещается в RAM без проблем).

    Caller'ы (middleware platform_admin_guard + endpoint dep
    get_current_identity) поднимают исключения `_introspect`'а ровно так
    же, как и без кэша — мы не глотаем 401/503, они просачиваются
    наверх без cache-side-effect'ов.
    """
    key = _token_cache_key(token)
    now = time.monotonic()

    cached = _identity_cache.get(key)
    if cached is not None:
        body, expires_at = cached
        if expires_at > now:
            return body
        # Stale — лениво evict'аем, чтобы не разрастаться при долгом uptime.
        _identity_cache.pop(key, None)

    body = await _introspect(token)
    if body.get("active"):
        _identity_cache[key] = (body, now + _INTROSPECT_CACHE_TTL_SECONDS)
    return body


# Module-level pooled client. Инициализируется в `main.lifespan` startup,
# закрывается в shutdown. Остаётся `None` outside the app lifecycle (например,
# при ранних импортах в тестах) — `_introspect` тогда падает на per-call
# client, чтобы тесты не сломать, но production path всегда идёт через пул.
_introspect_client: httpx.AsyncClient | None = None


def _extract_bearer(request: Request) -> str | None:
    """Достать сырое значение токена после префикса 'Bearer '."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


async def _introspect(token: str) -> dict:
    """Позвать introspect-endpoint auth_service. Сетевые ошибки → 503.

    Introspect защищён shared-secret'ом (`SERVICE_API_KEY`), отправляем его
    как `Authorization: Bearer <key>`. Без header'а auth_service ответит
    401 и мы поднимем 503 наружу (пользовательский bearer идёт в JSON body,
    а не в Authorization — auth_service читает его из `{"token": ...}`).

    Использует module-level pooled `_introspect_client` (поднятый FastAPI
    lifespan'ом в `main.py`). Outside the app lifecycle (например, ad-hoc
    unit-тесты, которые импортят модуль до startup) — fall back to per-call
    client, чтобы функция оставалась usable, но production request path
    всегда идёт через пул.

    Slowloris-mitigation (c): bearer-shape pre-check здесь, ДО любого
    HTTP-roundtrip'а. Токены, не похожие на реальные (`eyJ…` / `dbos_pat_…` /
    `dbos_bot_…`, длина >= 20) отсекаются как `INVALID_TOKEN_FORMAT`/401 —
    auth_service не дёргается зря.
    """
    if not _is_token_shape_valid(token):
        raise AuthenticationError(
            error_code="INVALID_TOKEN_FORMAT",
            message="Bearer token has invalid format",
        )

    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.service_api_key}"}

    client = _introspect_client
    try:
        if client is not None:
            # Pooled path: `base_url` уже на клиенте, посылаем relative path.
            response = await client.post(
                _INTROSPECT_PATH, json={"token": token}, headers=headers
            )
        else:
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


def _to_identity(body: dict) -> IdentityContext:
    """Маппинг introspect-ответа в наш IdentityContext.

    `subject_type` пробрасывается из introspect-ответа (`user` / `bot` / `pat` /
    `oauth_client`) — `audit_service.emit` потом подхватит как `actor_type`,
    чтобы worker_bot и OAuth-клиенты не смешивались с человеческими действиями
    в SIEM-логе.
    """
    return IdentityContext(
        user_id=body.get("sub") or "",
        username=body.get("username") or "",
        department_id=body.get("department_id"),
        department_name=body.get("department_name"),
        allowed_services=body.get("allowed_services", []),
        service_roles=body.get("service_roles", {}),
        is_banned=body.get("is_banned", False),
        platform_role=body.get("platform_role"),
        subject_type=body.get("subject_type"),
    )


async def get_current_identity(request: Request) -> IdentityContext:
    """Resolve identity вызывающего через introspect в auth_service.

    Инварианты, проверяемые ДО того, как endpoint вообще увидит запрос:
      1. Bearer есть и introspect вернул `active=true`;
      2. user не забанен;
      3. department имеет доступ к `server_service`. Platform-admin'ы
         (account_admin/loging_admin/loging_reader) отрезаются раньше —
         `platform_admin_guard` middleware кидает 403 ещё до dependency,
         так что сюда они в нормальном flow не дойдут. Здесь проверка —
         defence-in-depth: если middleware снимут, бизнес-данные всё
         равно недоступны без явной service-роли.
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(
            error_code="ACCESS_TOKEN_MISSING",
            message="Missing bearer token",
        )

    # Идём через TTL-кэш (5s) — middleware platform_admin_guard уже сделал
    # `_introspect` на этом же request'е, мы переиспользуем body, экономим
    # outbound HTTP-roundtrip. См. ``_get_or_cache_introspect`` docstring.
    body = await _get_or_cache_introspect(token)
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
    # Заполняем audit_context (request_id/ip/ua проставлены в middleware).
    # subject_type пробрасываем в context — `audit_service.emit` подхватит
    # как actor_type, чтобы worker_bot / OAuth не смешивались с user'ами.
    audit_context.update_context(
        actor_id=identity.user_id,
        username=identity.username,
        department_id=identity.department_id,
        subject_type=identity.subject_type,
    )
    return identity


CurrentIdentity = Annotated[IdentityContext, Depends(get_current_identity)]
