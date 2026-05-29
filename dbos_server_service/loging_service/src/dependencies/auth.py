"""Auth-зависимости loging_service.

Три уровня:
  `require_service_token` — shared `SERVICE_API_KEY` для service-to-service
                           вызовов (только POST events / register events).
  `require_admin`         — `loging_admin` JWT: полный доступ, включая правила.
  `require_reader`        — read-only: `loging_admin` / `loging_reader` /
                           `department_admin` / `account_admin`. `loging_reader`
                           и `department_admin` auto-scoped к своему отделу.

### Connection pool

`httpx.AsyncClient` — module-level (`_introspect_client`), управляется
FastAPI-`lifespan`'ом в `src/main.py`:

* startup → собирает один `AsyncClient` с `base_url=auth_service_url`,
  общим таймаутом, ограниченным pool'ом (`max_connections=20`,
  `max_keepalive_connections=10`) и `verify` из `INTROSPECT_TLS_VERIFY`
  (default `True`);
* shutdown → `await _introspect_client.aclose()`.

Зачем: каждый read-эндпоинт (`GET /events` / `GET /services` / `GET /rules`
/ …) уходит в `_fetch_identity()`. С per-call `httpx.post` slowloris-burst
из N параллельных reader-запросов открывает N свежих TCP+TLS handshake'ов
к auth_service, исчерпывая FD-пул на обеих сторонах и амплифицируя любой
slowdown auth_service в 503 fan-out. Один pooled client кэпает количество
исходящих коннектов и амортизирует TLS-handshake по запросам — симметрично `server_service`.

Fallback (`_introspect_client is None`) — эфемерный `AsyncClient`, открытый
и закрытый ровно на один запрос. Это путь для ad-hoc сценариев, где lifespan
не стартовал (например, прямой вызов `_fetch_identity` в тестах без
TestClient). Production всегда идёт через pool — lifespan отрабатывает до
первого запроса.
"""

import logging
import secrets
from typing import Annotated

import httpx
from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import get_settings
from src.core.exceptions import AppException

_logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# Роли, scoped к своему отделу.
#
# По platform-модели auth_service `loging_reader` создаётся БЕЗ
# `department_id` (как `loging_admin` / `account_admin`) и описан как
# «чтение аудит-событий по всем департаментам». Здесь же он попадает в
# dept-scope гард: без `department_id` `require_reader` отдаёт
# 403 NO_DEPARTMENT.
#
# Это сознательная локальная политика loging_service'а: даже platform-роль
# «reader» сужается до своего отдела, чтобы read-доступ не утекал
# кросс-департаментно через одну ошибку выдачи прав. Полное «видеть всё»
# остаётся за `loging_admin` и `account_admin`.
#
# Если когда-нибудь понадобится глобальный read без admin-привилегий —
# вынести `loging_reader` из `_DEPT_SCOPED_ROLES` и вернуть
# `_dept_scope=None` в `require_reader`. Тест-контракт фиксирующий текущее
# поведение: `tests/test_cov_focus.py::TestDeptScopedRolesQuirk`.
_DEPT_SCOPED_ROLES = {"loging_reader", "department_admin"}

# Сестринские сервисы, которые мы ожидаем увидеть в `X-Service-Identity`, когда
# внутренний caller дёргает service-token эндпоинт. Зеркалит аналогичный set
# в `auth_service/src/core/constants.py`. Cross-tenant гарды полагаются на это
# как на канонический allow-list — добавляешь новый внутренний сервис, который
# дёргает loging_service по service-token? Добавь его и сюда.
KNOWN_SERVICE_IDENTITIES: frozenset[str] = frozenset(
    {"auth_service", "server_service", "config_service", "server_worker"}
)


# Module-level pooled client. Инициализируется в `main.lifespan` (startup),
# закрывается в shutdown. Остаётся `None` вне app-lifecycle (например, при
# раннем импорте в ad-hoc тестах, где lifespan не запускается) —
# `_fetch_identity` в этом случае открывает эфемерный `AsyncClient` ровно
# на один запрос (см. ниже). Production request-path всегда через pool —
# lifespan отрабатывает до первого запроса.
_introspect_client: httpx.AsyncClient | None = None

# Pooled клиент для проксирования Swagger-логина (`POST /token`). Тоже
# управляется lifespan'ом; live-сессия read/write делит общий таймаут с
# introspect'ом, но connect-таймаут берёт из `INTROSPECT_CONNECT_TIMEOUT_SECONDS`.
# Без pool'а каждый swagger-login открывал бы свежий TCP+TLS handshake.
_token_proxy_client: httpx.AsyncClient | None = None

# Где живёт introspect на auth_service. Pooled-клиент использует
# base_url + этот относительный путь; fallback конкатенирует с
# `settings.auth_service_url`.
_INTROSPECT_PATH = "/api/auth/v1/authorization/introspect"


def require_service_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """Service-to-service гард для ingest-эндпоинтов.

    Два режима, выбираются автоматически по `SERVICE_API_KEYS`:

    **Per-service mode** (`SERVICE_API_KEYS` непустой): JSON-env мапит
    каждую service-identity на свой bearer-secret. `X-Service-Identity`
    обязателен, работает ключом lookup'а; сравнение timing-safe.
    Identity вне map'а отвергается, даже если она в
    :data:`KNOWN_SERVICE_IDENTITIES` (map — operator allow-list).
    Закрывает «компрометация shared key → forge any service».

    **Shared mode** (`SERVICE_API_KEYS` пустой — legacy single-key):
    fallback на `SERVICE_API_KEY` + soft/strict allow-list для
    `X-Service-Identity`. Backward compatible.

    Семантика header'а в per-identity mode:

    * Нет → 401 `MISSING_SERVICE_IDENTITY` (без identity не выбрать ключ).
    * Есть и в map'е → `compare_digest(provided, keys[identity])` → 401
      `INVALID_SERVICE_TOKEN` на mismatch, иначе stash identity в
      `request.state.service_identity`, чтобы эндпоинты могли сделать
      path-vs-identity check (`endpoints/services.py`).
    * Есть, но НЕТ в map'е → 401 `UNKNOWN_SERVICE_IDENTITY`
      (намеренно — map это операционный allow-list).

    Семантика shared mode: soft пропускает missing/unknown headers
    (WARNING'ом), strict — 401.
    """
    settings = get_settings()

    # Локальный импорт — иначе circular dependency на module-import.
    from src.utils.normalization import normalize_service_name

    # ── Per-service-key path ────────────────────────────────────────────
    if settings.service_api_keys:
        if credentials is None:
            raise AppException(
                http_status=401,
                error_code="INVALID_SERVICE_TOKEN",
                message="Valid service API key required",
            )

        raw_identity = request.headers.get("X-Service-Identity")
        if raw_identity is None:
            raise AppException(
                http_status=401,
                error_code="MISSING_SERVICE_IDENTITY",
                message=(
                    "X-Service-Identity header is required when per-service "
                    "API keys are configured"
                ),
            )

        identity = normalize_service_name(raw_identity)
        if not identity:
            identity = "<empty>"

        expected_key = settings.service_api_keys.get(identity)
        if expected_key is None:
            # Timing-oracle защита: known/unknown identity должны давать
            # одинаковую latency, иначе атакующий probit'ит список
            # сконфигурированных identity по разнице времени ответа. Прогоняем
            # compare_digest против фиктивного секрета той же длины, что и
            # реальные ключи (берём первый из map для длины — все ключи
            # должны быть сопоставимы; иначе фолбэк на 32 байта).
            sample_key = next(iter(settings.service_api_keys.values()), "x" * 32)
            secrets.compare_digest(credentials.credentials, sample_key)
            _logger.warning(
                "loging: X-Service-Identity %r is not present in "
                "SERVICE_API_KEYS map (path=%s) — rejecting",
                raw_identity,
                request.url.path,
            )
            raise AppException(
                http_status=401,
                error_code="UNKNOWN_SERVICE_IDENTITY",
                message=(
                    "X-Service-Identity does not match any configured "
                    "per-service API key"
                ),
            )

        if not secrets.compare_digest(credentials.credentials, expected_key):
            raise AppException(
                http_status=401,
                error_code="INVALID_SERVICE_TOKEN",
                message="Valid service API key required",
            )

        request.state.service_identity = identity
        return

    # ── Legacy shared-key path (backward-compat) ─────────────────────────
    if credentials is None or not secrets.compare_digest(
        credentials.credentials, settings.service_api_key
    ):
        raise AppException(
            http_status=401,
            error_code="INVALID_SERVICE_TOKEN",
            message="Valid service API key required",
        )

    # ── X-Service-Identity валидация (mTLS-partial) ─────────────────────
    raw_identity = request.headers.get("X-Service-Identity")
    if raw_identity is None:
        if settings.strict_service_identity:
            raise AppException(
                http_status=401,
                error_code="MISSING_SERVICE_IDENTITY",
                message=(
                    "X-Service-Identity header is required "
                    "(STRICT_SERVICE_IDENTITY=true)"
                ),
            )
        # Soft mode — backward-compat для caller'ов, которые не знают про header.
        return

    # Нормализуем (NFKC + invisibles strip + confusables fold + lower) до и
    # allow-list проверки, и stash'а, чтобы downstream path-vs-identity
    # сравнение в эндпоинте могло делать plain `==` без повторной
    # нормализации. Зеркалит `endpoints/services.py`, который тоже
    # `normalize_service_name`'ит path-параметр.
    identity = normalize_service_name(raw_identity)
    if not identity:
        identity = "<empty>"

    if identity in KNOWN_SERVICE_IDENTITIES:
        request.state.service_identity = identity
        return

    _logger.warning(
        "loging: unknown X-Service-Identity header value=%r path=%s; "
        "valid SERVICE_API_KEY present, request %s",
        raw_identity,
        request.url.path,
        "REJECTED (STRICT_SERVICE_IDENTITY=true)"
        if settings.strict_service_identity
        else "allowed (soft mode)",
    )
    if settings.strict_service_identity:
        raise AppException(
            http_status=401,
            error_code="INVALID_SERVICE_IDENTITY",
            message=(
                "X-Service-Identity header does not match any known "
                "service in the allow-list"
            ),
        )


async def _fetch_identity(
    credentials: HTTPAuthorizationCredentials | None, request: Request
) -> dict:
    """Общий helper: introspect токена в auth_service, identity в `request.state`.

    Использует `POST /authorization/introspect` (единственный source of
    truth для любого bearer: user JWT, PAT, bot token). Introspect-ответ
    кладёт subject id в `sub`; мы экспозим его как `user_id`, чтобы остальной
    loging_service не трогать (audit middleware, эндпоинт-хендлеры).

    Production-path — pooled `_introspect_client`. Fallback (ad-hoc
    тесты, где lifespan не запускался) — эфемерный `AsyncClient` ровно
    на один запрос (open → POST → aclose). Sync `httpx.post` нигде не
    используется.
    """
    if credentials is None:
        raise AppException(http_status=401, error_code="MISSING_TOKEN",
                           message="Authentication required")

    settings = get_settings()
    if not settings.auth_service_url:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_NOT_CONFIGURED",
                           message="AUTH_SERVICE_URL is not configured")

    # auth_service /introspect защищён `require_service_token` — нам надо
    # послать shared `SERVICE_API_KEY` в Authorization header. Bearer
    # пользователя (`credentials.credentials`) идёт в JSON body для
    # introspect'а.
    #
    # `X-Service-Identity` — self-identification caller'а. Introspect-guard
    # на auth_service сейчас проверяет только `SERVICE_API_KEY` (один
    # shared secret на все сервисы), так что этот header сегодня
    # информационный. Он нужен как wire-format hook для будущего
    # per-caller mTLS / per-service-key (auth_service сможет требовать,
    # чтобы client-cert / API-key совпадал с заявленной identity). См.
    # TODO «single SERVICE_API_KEY → per-service key».
    payload = {"token": credentials.credentials}
    # Outbound introspect-ключ. Если задан отдельный `INTROSPECT_SERVICE_API_KEY` —
    # используем его (раздельные ключи для ingest- и introspect-каналов); иначе
    # фолбэк на shared `SERVICE_API_KEY` (legacy single-key стенд).
    introspect_key = settings.introspect_service_api_key or settings.service_api_key
    headers = {
        "Authorization": f"Bearer {introspect_key}",
        "X-Service-Identity": "loging_service",
    }

    client = _introspect_client
    try:
        if client is not None:
            # Pooled (production) path: base_url выставлен на клиенте.
            resp = await client.post(_INTROSPECT_PATH, json=payload, headers=headers)
        else:
            # Fallback (ad-hoc, lifespan не запускался): эфемерный AsyncClient
            # ровно на один запрос. Sync `httpx.post` не используем —
            # вне event-loop'а live-сессии это блокировало бы threadpool,
            # а внутри loop'а sync вызов недоступен совсем. Эфемерный
            # клиент дороже pooled'а (один handshake), но это test/ad-hoc
            # путь, не hot-path.
            base = settings.auth_service_url.rstrip("/")
            timeout = httpx.Timeout(
                settings.introspect_timeout_seconds,
                connect=settings.introspect_connect_timeout_seconds,
            )
            async with httpx.AsyncClient(
                timeout=timeout,
                verify=settings.introspect_tls_verify,
            ) as ephemeral:
                resp = await ephemeral.post(
                    f"{base}{_INTROSPECT_PATH}",
                    json=payload,
                    headers=headers,
                )
    except httpx.TimeoutException:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_TIMEOUT",
                           message="Auth service did not respond in time")
    except httpx.ConnectError:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_UNREACHABLE",
                           message="Unable to connect to auth service")
    except Exception as exc:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_ERROR",
                           message=f"Unexpected error: {type(exc).__name__}")

    if resp.status_code != 200:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_ERROR",
                           message=f"Auth service returned {resp.status_code}")

    body = resp.json()
    if not body.get("active"):
        raise AppException(http_status=401, error_code="INVALID_TOKEN",
                           message="Invalid, expired or revoked token")
    if body.get("is_banned"):
        raise AppException(http_status=401, error_code="USER_BANNED",
                           message="User is banned")

    # Приводим introspect-ответ к dict, который ждёт остальной loging_service.
    identity = dict(body)
    identity["user_id"] = body.get("sub")  # алиас для caller'ов (main.py, rules.py)

    # `actor_type` пробрасывание. auth_service introspect возвращает
    # `subject_type` ∈ {"user", "bot", "oauth_client"}; остальной
    # loging_service говорит `actor_type` ({"user", "bot", "service",
    # "anonymous", "oauth_client"}). Маппим 1:1. Без этого M2M caller'ы
    # (PAT/bot/oauth m2m) писались бы `actor_type="user"`, и SOC видел
    # бы fake user-активность на каждом service-вызове.
    # Anonymous (без токена) сюда не попадает — обрабатывается в
    # `main._emit_audit`, где `identity is None`.
    subject_type = body.get("subject_type")
    if subject_type in ("user", "bot", "oauth_client", "service"):
        identity["actor_type"] = subject_type
    else:
        # Старый introspect мог не присылать `subject_type` — backward-compat.
        identity["actor_type"] = "user"

    # Сохраняем ДО role-check'а, чтобы у audit-middleware всегда был actor.
    request.state.auth_identity = identity

    # Также проверяем service-роли в `loging_service` (у пользователя может
    # быть reader через service-role систему).
    loging_svc_roles = identity.get("service_roles", {}).get("loging_service", [])
    identity["_loging_service_roles"] = loging_svc_roles

    return identity


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """Требует `platform_role=loging_admin`. Полный доступ, включая правила."""
    identity = await _fetch_identity(credentials, request)
    if identity.get("platform_role") != "loging_admin":
        raise AppException(
            http_status=403,
            error_code="INSUFFICIENT_ROLE",
            message="platform_role=loging_admin is required to manage loging_service",
        )
    identity["_dept_scope"] = None  # loging_admin видит всё
    return identity


async def require_reader(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """Read-only доступ. Допустимые роли:

      `loging_admin`    → без dept-scope (видит всё)
      `account_admin`   → без dept-scope (видит всё)
      `loging_reader`   → scoped к своему `department_id`
      `department_admin`→ scoped к своему `department_id`
      любой пользователь с reader/operator/admin service-ролью в
      `loging_service` → scoped к своему отделу
    """
    identity = await _fetch_identity(credentials, request)
    role = identity.get("platform_role")
    loging_roles = identity.get("_loging_service_roles", [])

    # Определяем доступ и scope.
    if role in ("loging_admin", "account_admin"):
        identity["_dept_scope"] = None
        return identity

    if role in _DEPT_SCOPED_ROLES or any(r in loging_roles for r in ("reader", "operator", "admin")):
        # Scoped к собственному отделу.
        dept_id = identity.get("department_id")
        if not dept_id:
            raise AppException(
                http_status=403, error_code="NO_DEPARTMENT",
                message="User has no department assigned — cannot scope log access",
            )
        identity["_dept_scope"] = dept_id
        return identity

    raise AppException(
        http_status=403,
        error_code="INSUFFICIENT_ROLE",
        message=(
            "Access requires: platform_role in (loging_admin, loging_reader, "
            "account_admin, department_admin) or reader/operator/admin role in loging_service"
        ),
    )


AdminIdentity = Annotated[dict, Depends(require_admin)]
ReaderIdentity = Annotated[dict, Depends(require_reader)]
