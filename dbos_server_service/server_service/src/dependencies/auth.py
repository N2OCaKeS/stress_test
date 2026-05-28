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

### Без кэша introspect, но с дедупликацией внутри одного запроса

Результат introspect намеренно НЕ кэшируется между запросами — отозванный
или забаненный PAT/bot-токен должен переставать работать в тот же момент.
А вот внутри одного и того же запроса `platform_admin_guard` middleware
и endpoint-dependency `get_current_identity` раньше делали по своему
introspect'у — два сетевых roundtrip'а на один защищённый запрос.

Сейчас middleware кладёт свежий body в `request.state.introspect_body`,
а `get_current_identity` сначала читает оттуда; `_introspect(token)`
вызывается только если в state ничего нет (запрос пришёл мимо
middleware — например, через TestClient без full app-stack). Контракт
немедленного revoke сохраняется: middleware всё равно делает свежий
introspect на каждый запрос, кэш не появляется.
"""

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
from src.schemas.identity import IdentityContext
from src.services import audit_context

__all__ = [
    "CurrentIdentity",
    "SERVICE_NAME",
    "get_current_identity",
]

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

    # Дедупликация per-request: middleware `platform_admin_guard` уже
    # сделал свежий introspect и положил body в `request.state`. Если он там
    # есть — переиспользуем; иначе зовём `_introspect` (это путь для запросов,
    # которые прошли мимо middleware — например, ad-hoc TestClient-вызовы
    # без full ASGI-stack'а). Контракт мгновенного revoke сохранён: между
    # запросами state не переживает, на каждом запросе introspect свежий.
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
