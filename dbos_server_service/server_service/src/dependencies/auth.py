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

import hmac
import logging
from typing import Annotated, Callable

import httpx
from fastapi import Depends, Header, Request

from src.core.config import get_settings
from src.core.constants import PlatformRole, SERVICE_NAME
from src.core.http import bearer_header
from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ServiceUnavailableError,
)
from src.schemas.identity import IdentityContext
from src.services import audit_context

logger = logging.getLogger(__name__)

__all__ = [
    "AccountAdminIdentity",
    "AuthenticatedIdentity",
    "CurrentIdentity",
    "CurrentUserIdentity",
    "SERVICE_NAME",
    "get_authenticated_identity",
    "get_current_identity",
    "require_account_admin",
    "require_internal_caller",
    "require_user_context",
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
    # X-Service-Identity нужен auth_service, чтобы выбрать наш bearer из
    # своего SERVICE_API_KEYS по идентичности server_service (без него
    # auth_service вернёт 401 MISSING_SERVICE_IDENTITY в per-service режиме).
    headers = {
        **bearer_header(settings.service_api_key),
        "X-Service-Identity": "server_service",
    }

    client = _introspect_client
    try:
        if client is not None:
            # Pooled path: `base_url` уже на клиенте, посылаем relative path.
            response = await client.post(
                _INTROSPECT_PATH, json={"token": token}, headers=headers
            )
        else:
            # Lifespan уже снёс pool (или unit-тест, который ходит мимо
            # lifespan'а) — каждый запрос здесь поднимает свежий TCP+TLS,
            # медленнее и без slowloris-bound'а. В нормальном проде сюда
            # попадать не должны; debug-лог даёт сигнал, если попали.
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


def _to_identity(body: dict) -> IdentityContext:
    """Маппинг introspect-ответа в наш IdentityContext.

    `subject_type` пробрасывается из introspect-ответа (`user` / `bot` /
    `oauth_client`) — `audit_service.emit` потом подхватит как `actor_type`,
    чтобы worker_bot и OAuth-клиенты не смешивались с человеческими действиями
    в SIEM-логе. PAT-токены auth_service маппит на subject_type=`user`
    (владелец токена — обычный пользователь), отдельного значения для них нет.
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
        department_name=identity.department_name,
        subject_type=identity.subject_type,
    )
    return identity


CurrentIdentity = Annotated[IdentityContext, Depends(get_current_identity)]


async def get_authenticated_identity(request: Request) -> IdentityContext:
    """Resolve identity без проверки доступа департамента к server_service.

    Облегчённый вариант `get_current_identity` для глобального каталога
    OS-версий: читать его может любой аутентифицированный актор, включая
    платформенные роли (`account_admin`/`loging_admin`), у которых нет
    `department_id` и нет server_service в `allowed_services`. Поэтому
    `SERVICE_ACCESS_DENIED`-гейт здесь намеренно не проверяется.

    Проверяется только:
      1. Bearer есть (нет → 401 ACCESS_TOKEN_MISSING);
      2. introspect вернул `active=true` (нет → 401 ACCESS_TOKEN_INVALID);
      3. актор не забанен.

    Анонимный запрос (без токена) отбивается 401 — каталог открыт всем
    аутентифицированным, но не анонимам. Запись в каталог по-прежнему идёт
    через `CurrentUserIdentity` + матрицу прав.

    `platform_admin_guard` middleware на GET `/os-versions*` не блокирует
    платформенные роли (путь exempt от business-блока), но introspect там
    не делается — поэтому здесь body берётся из `request.state` если есть,
    иначе зовём `_introspect` сами.
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
        subject_type=identity.subject_type,
    )
    return identity


AuthenticatedIdentity = Annotated[IdentityContext, Depends(get_authenticated_identity)]


# ── User-facing guard: отбивает OAuth m2m identity ──────────────────────────


def require_user_context(
    identity: Annotated[IdentityContext, Depends(get_current_identity)],
) -> IdentityContext:
    """Отбивает m2m identity (OAuth `client_credentials`) на user-facing endpoint'ах.

    Симметрия с `auth_service.require_user_context` и `secret_service.require_user_context`:
    OAuth-клиенту нет места в user-facing бизнес-эндпоинтах server_service —
    servers/server_accounts/ipmi/inventory/etc оперируют пользовательскими
    разрешениями по матрице `entity_permissions`, у `oauth_client` нет ни
    `department_id`, ни мест в матрице (его user_id формата `cli_*` улетал бы
    в audit-trail и FK-операции).

    Без этого guard'а m2m-токен сегодня проходил `get_current_identity` (он
    лишь проверяет active + banned + allowed_services) и упирался в матрицу
    permissions на бизнес-слое — менее удобно для аудита (несколько слоёв
    проходит, прежде чем 403), и в `audit_context.actor_id` уже зафиксирован
    `cli_*` ID. Здесь режем с 403 `USER_CONTEXT_REQUIRED` сразу после
    introspect'а.

    `/internal/*` (worker→server_service), `/internal/secrets/*`
    (worker→server_service ротация) и `ops` (s2s через shared-secret) этот
    guard НЕ используют — там identity либо worker_bot (subject_type='bot'),
    либо service-identity без user-introspect'а вообще.

    Применяется через `Depends(require_user_context)` либо на уровне router'а
    через `APIRouter(..., dependencies=[Depends(require_user_context)])`.
    """
    if identity.subject_type == "oauth_client":
        raise AuthorizationError(
            error_code="USER_CONTEXT_REQUIRED",
            message=(
                "This endpoint requires user context; "
                "OAuth client_credentials tokens are not accepted"
            ),
        )
    return identity


CurrentUserIdentity = Annotated[IdentityContext, Depends(require_user_context)]


# ── Platform admin (encryption-key rotation) ────────────────────────────────


async def require_account_admin(request: Request) -> IdentityContext:
    """Resolve identity и пропустить ТОЛЬКО `account_admin`.

    Отдельный путь от `get_current_identity`: тот требует
    `server_service in allowed_services`, а у платформенного `account_admin`
    нет ни департамента, ни department-service-access — обычный business-flow
    отбил бы его 403 SERVICE_ACCESS_DENIED. Здесь проверяем лишь active +
    not banned + `platform_role == account_admin`.

    Используется инфраструктурными admin-эндпоинтами ротации ключей шифрования
    (`/admin/encryption/*`) — они не возвращают бизнес-данные, только статус
    ротации и версии ключа, поэтому это явное исключение из
    `platform_admin_guard` business-data-блока. Сам guard пропускает эти пути
    по allowlist'у (см. `middleware/platform_admin_guard._is_admin_encryption_path`),
    а здесь — позитивная проверка роли.

    Body introspect'а переиспользуется из `request.state.introspect_body`
    (его кладёт middleware) — без второго roundtrip'а; на запросах мимо
    middleware зовём `_introspect` сами.
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
    if identity.platform_role != PlatformRole.ACCOUNT_ADMIN:
        raise AuthorizationError(
            error_code="ACCOUNT_ADMIN_REQUIRED",
            message="account_admin platform role required",
        )
    audit_context.update_context(
        actor_id=identity.user_id,
        username=identity.username,
        department_id=identity.department_id,
        department_name=identity.department_name,
        subject_type=identity.subject_type,
    )
    return identity


AccountAdminIdentity = Annotated[IdentityContext, Depends(require_account_admin)]


# ── Service-to-service (ops endpoints) ──────────────────────────────────────


def require_internal_caller(
    *allowed_identities: str,
) -> Callable[[Request, str | None], str]:
    """Dependency factory под ops-эндпоинты с shared-secret авторизацией.

    Возвращает FastAPI-зависимость, которая:

    1. Читает `X-Service-Identity` header (без него → 401 SERVICE_IDENTITY_REQUIRED).
    2. Проверяет, что identity входит в whitelist `allowed_identities` для роута
       (вне списка → 403 SERVICE_IDENTITY_NOT_ALLOWED — не утекаем существование
       чужих identity, но отдаём отдельный код, чтобы оператор отличал misconfig
       от unknown identity).
    3. Достаёт ожидаемый ключ из `settings.service_api_keys[identity]`. Если для
       identity ключ не сконфигурирован — 401 INVALID_SERVICE_TOKEN (тот же код,
       что и для несовпадения, чтобы по разнице 401 нельзя было перечислить, какие
       identity у нас сконфигурированы).
    4. Сравнивает `Authorization: Bearer <token>` constant-time'ом
       (`hmac.compare_digest`). Несовпадение / отсутствие → 401 INVALID_SERVICE_TOKEN.

    Возвращает identity (str) — endpoint может писать его в audit/details, не
    дублируя парсинг.

    Этот dependency НЕ ходит в auth_service introspect: rotation_runner и
    подобные ops-runner'ы — отдельный s2s-канал, без user identity / department.
    Они не входят в матрицу `entity_permissions`, поэтому защита через scope-
    grant'ы (как `_require_worker_scope` в `secrets_migration.py`) тут не
    применима — нужен именно shared-secret check.
    """

    if not allowed_identities:
        raise ValueError(
            "require_internal_caller: allowed_identities must be non-empty"
        )
    allowed = frozenset(allowed_identities)

    async def _check(
        request: Request,
        x_service_identity: str | None = Header(
            default=None,
            alias="X-Service-Identity",
            description=(
                "Идентичность вызывающего ops-сервиса (например, `rotation_runner`)."
                " Используется ops-эндпоинтами вместо user-introspect'а; вместе с"
                " Authorization-bearer сверяется с `SERVICE_API_KEYS[<identity>]`."
            ),
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
                message=(
                    f"Service identity {identity!r} is not allowed to call this endpoint"
                ),
            )
        token = _extract_bearer(request)
        if not token:
            raise AuthenticationError(
                error_code="INVALID_SERVICE_TOKEN",
                message="Missing or malformed bearer token for service identity",
            )
        expected = get_settings().service_api_keys.get(identity)
        if not expected:
            # Identity whitelist'ом разрешён, но ключ не сконфигурирован —
            # это deployment misconfig. Не отдаём отдельный код, чтобы по
            # разнице 401 нельзя было перечислить configured-identity.
            raise AuthenticationError(
                error_code="INVALID_SERVICE_TOKEN",
                message="Service token is invalid",
            )
        if not hmac.compare_digest(token, expected):
            raise AuthenticationError(
                error_code="INVALID_SERVICE_TOKEN",
                message="Service token is invalid",
            )
        # Пишем identity в audit_context как pseudo-actor — emit'ы внутри
        # ops-handler'ов получат `actor_id=ops:<identity>` без отдельной
        # ручной возни в каждом endpoint'е.
        audit_context.update_context(
            actor_id=f"ops:{identity}",
            username=identity,
            subject_type="service",
        )
        return identity

    return _check
