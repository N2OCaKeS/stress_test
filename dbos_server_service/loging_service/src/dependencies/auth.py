"""Auth-зависимости loging_service.

Три уровня:
  `require_service_token` — per-service `SERVICE_API_KEYS` map для
                           service-to-service вызовов (POST events / register
                           events). Identity определяется из map по
                           предъявленному ключу + обязательному
                           `X-Service-Identity` header'у.
  `require_admin`         — `loging_admin` JWT: полный доступ, включая правила.
  `require_reader`        — read-only: `loging_admin` / `loging_reader` /
                           `account_admin`. Все три видят весь журнал cross-dept
                           (dept-scope не применяется). `account_admin` получает
                           только чтение (events / stats / export / каталог) —
                           правила и retention остаются за `loging_admin`.
                           `department_admin` к чтению аудита не допускается:
                           если dep_admin'у нужно читать журнал своего отдела,
                           ему явно выдаётся платформенная роль `loging_reader`.

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

Pool обязателен: если `_introspect_client is None` при request-path
(lifespan не запускался — ad-hoc/прямой вызов вне TestClient'а),
`_fetch_identity` поднимает 503 `INTROSPECT_NOT_INITIALIZED`. Эфемерного
fallback'а нет: production всегда идёт через lifespan; тесты, которым
нужен introspect, обязаны подменить pool monkeypatch'ом.
"""

import logging
import secrets
from typing import Annotated

import httpx
from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.http import bearer_header

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# `KNOWN_SERVICE_IDENTITIES` снят: в per-service-only режиме allow-list — это
# сам `SERVICE_API_KEYS` map. Identity вне map'а отвергается на этапе
# `require_service_token`; отдельный hard-coded set дублировал бы операторскую
# конфигурацию и приводил к расхождениям при добавлении нового внутреннего
# сервиса.
#
# Dept-scope для read-роли: НЕТ. К чтению audit'а допускаются `loging_admin`,
# `loging_reader` и `account_admin` — все три платформенные роли создаются без
# `department_id` и видят журнал cross-dept целиком. `account_admin` ограничен
# чтением (mutation правил/retention за `require_admin`). `department_admin`
# к loging_service не допускается. Историческая dept-scoped семантика снята:
# ни `_DEPT_SCOPED_ROLES`, ни `identity["_dept_scope"]` больше не нужны —
# вызывающие эндпоинты репозиториям dept-фильтр не пробрасывают.

# Module-level pooled client. Инициализируется в `main.lifespan` (startup),
# закрывается в shutdown. Остаётся `None` вне app-lifecycle — в этом случае
# `_fetch_identity` отбивает 503 `INTROSPECT_NOT_INITIALIZED`. Production
# request-path всегда через pool, lifespan отрабатывает до первого запроса.
_introspect_client: httpx.AsyncClient | None = None

# Pooled клиент для проксирования Swagger-логина (`POST /token`). Тоже
# управляется lifespan'ом; live-сессия read/write делит общий таймаут с
# introspect'ом, но connect-таймаут берёт из `INTROSPECT_CONNECT_TIMEOUT_SECONDS`.
# Без pool'а каждый swagger-login открывал бы свежий TCP+TLS handshake.
_token_proxy_client: httpx.AsyncClient | None = None

# Где живёт introspect на auth_service. Pooled-клиент использует
# base_url + этот относительный путь.
_INTROSPECT_PATH = "/api/auth/v1/authorization/introspect"


def require_service_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """Service-to-service гард для ingest-эндпоинтов.

    Per-service-only режим: `SERVICE_API_KEYS` JSON map'ит каждую
    service-identity на свой bearer-secret. `X-Service-Identity` обязателен
    и работает ключом lookup'а; сравнение `compare_digest`'ом — timing-safe.
    Identity вне map'а отвергается (map — operator allow-list).

    Семантика:

    * `SERVICE_API_KEYS` пуст → 401 `INVALID_SERVICE_KEY`
      (deployment-misconfig; раньше отвечали 503 SERVICE_TOKEN_NOT_CONFIGURED,
      но снаружи это утечка информации о состоянии конфигурации: атакующий
      по 503-ответу узнаёт, что ingest вообще выключен, и переходит на
      другой вектор. 401 единообразно отвечает «ключ невалиден» — внутреннее
      состояние не утекает, оператор всё равно видит проблему по
      `SERVICE_API_KEYS`-валидации на startup'е и self-audit-failure
      counter'у). Лог сохраняет диагностику.
    * Нет `Authorization` → 401 `INVALID_SERVICE_KEY`.
    * Нет `X-Service-Identity` → 401 `MISSING_SERVICE_IDENTITY`.
    * Identity нормализована и НЕ в map'е → 401 `INVALID_SERVICE_KEY`
      (timing-safe сравнение против sample-key, чтобы не утечь список
      сконфигурированных identity по разнице latency).
    * Identity в map'е, но `compare_digest` не сошёлся → 401
      `INVALID_SERVICE_KEY`.
    * Match → identity stash'ится в `request.state.service_identity`
      (downstream rate-limit key и path-vs-identity check).
    """
    settings = get_settings()

    # Локальный импорт — иначе circular dependency на module-import.
    from src.utils.normalization import normalize_service_name

    if not settings.service_api_keys:
        # Конфиг-промах: ingest невозможен ни от какого сервиса. Production
        # guard уже отбивает это на старте; здесь — для local/dev, где
        # оператор может прокинуть пустой map'ы случайно. Унифицируем ответ
        # с остальными невалидно-ключ ветками (401 INVALID_SERVICE_KEY): 503
        # снаружи выдавал бы атакующему «ingest выключен», и тот переходил
        # бы на другой вектор. В лог пишем подсказку, чтобы оператор видел
        # отличие misconfig от реального перебора ключа.
        logger.warning(
            "SERVICE_API_KEYS map is empty — rejecting service-to-service ingest "
            "(path=%s, method=%s)",
            request.url.path, request.method,
        )
        raise AppException(
            http_status=401,
            error_code="INVALID_SERVICE_KEY",
            message="Invalid service API key",
        )

    if credentials is None:
        raise AppException(
            http_status=401,
            error_code="INVALID_SERVICE_KEY",
            message="Valid service API key required",
        )

    raw_identity = request.headers.get("X-Service-Identity")
    if raw_identity is None:
        raise AppException(
            http_status=401,
            error_code="MISSING_SERVICE_IDENTITY",
            message="X-Service-Identity header is required",
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
        # Это ожидаемый bot/scan-трафик (header-fuzz, recon), не операционная
        # аномалия. WARNING на каждое unknown identity создавал false-positive
        # spike в alerting под массовым сканом. Реальные misconfig'и видны
        # 401-ом в audit-канале (`http.access_denied`), который уровнем выше.
        logger.debug(
            "loging: X-Service-Identity %r is not present in "
            "SERVICE_API_KEYS map (path=%s) — rejecting",
            raw_identity,
            request.url.path,
        )
        raise AppException(
            http_status=401,
            error_code="INVALID_SERVICE_KEY",
            message="Invalid service API key",
        )

    if not secrets.compare_digest(credentials.credentials, expected_key):
        raise AppException(
            http_status=401,
            error_code="INVALID_SERVICE_KEY",
            message="Invalid service API key",
        )

    request.state.service_identity = identity


async def _fetch_identity(
    credentials: HTTPAuthorizationCredentials | None, request: Request
) -> dict:
    """Общий helper: introspect токена в auth_service, identity в `request.state`.

    Использует `POST /authorization/introspect` (единственный source of
    truth для любого bearer: user JWT, PAT, bot token). Introspect-ответ
    кладёт subject id в `sub`; мы экспозим его как `user_id`, чтобы остальной
    loging_service не трогать (audit middleware, эндпоинт-хендлеры).

    Production-path — pooled `_introspect_client`. Если pool не поднят
    (lifespan не запускался — ad-hoc вызов вне TestClient'а), отдаём
    503 `INTROSPECT_NOT_INITIALIZED`. Тесты, которым нужен introspect,
    обязаны подменить pool monkeypatch'ом.
    """
    if credentials is None:
        raise AppException(http_status=401, error_code="MISSING_TOKEN",
                           message="Authentication required")

    settings = get_settings()
    if not settings.auth_service_url:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_NOT_CONFIGURED",
                           message="AUTH_SERVICE_URL is not configured")

    # auth_service /introspect защищён `require_service_token` — шлём наш
    # outbound introspect-ключ в Authorization header. Bearer пользователя
    # (`credentials.credentials`) идёт в JSON body для introspect'а.
    #
    # `X-Service-Identity: loging_service` self-identification caller'а:
    # auth_service сверит его со своим `SERVICE_API_KEYS["loging_service"]`
    # и `compare_digest` с предъявленным ключом.
    payload = {"token": credentials.credentials}
    if not settings.introspect_service_api_key:
        raise AppException(
            http_status=503,
            error_code="INTROSPECT_KEY_NOT_CONFIGURED",
            message="INTROSPECT_SERVICE_API_KEY is not set",
        )
    headers = {
        **bearer_header(settings.introspect_service_api_key),
        "X-Service-Identity": "loging_service",
    }

    client = _introspect_client
    if client is None:
        raise AppException(
            http_status=503,
            error_code="INTROSPECT_NOT_INITIALIZED",
            message="Introspect HTTP client is not initialised (lifespan did not run)",
        )
    try:
        resp = await client.post(_INTROSPECT_PATH, json=payload, headers=headers)
    except httpx.TimeoutException:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_TIMEOUT",
                           message="Auth service did not respond in time")
    except httpx.ConnectError:
        raise AppException(http_status=503, error_code="AUTH_SERVICE_UNREACHABLE",
                           message="Unable to connect to auth service")
    except Exception as exc:
        # exc-type ушёл бы наружу через message — клиент бы видел
        # `RemoteProtocolError` / `WriteError` и узнавал детали транспортной
        # ошибки auth_service. Симметрично non-200 ветке ниже: наружу
        # константная фраза, имя класса остаётся в логе для SRE.
        logger.warning("introspect raised unexpected exception: %s", type(exc).__name__)
        raise AppException(http_status=503, error_code="AUTH_SERVICE_ERROR",
                           message="Authentication service error")

    if resp.status_code != 200:
        # Upstream HTTP-статус из introspect не уходит наружу — это
        # информация о внутренней инфре auth_service (вверх по стеку
        # клиент видит 503 от нас и не должен различать «auth вернул 500»
        # и «auth вернул 502»). Детальный статус — только в лог для SRE.
        logger.warning(
            "introspect returned non-200 status: %d", resp.status_code,
        )
        raise AppException(http_status=503, error_code="AUTH_SERVICE_ERROR",
                           message="Authentication service error")

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
    # `main._emit_audit`, где `identity is None`. `service` тоже не
    # приходит через introspect: service-to-service caller'ы аутенти-
    # фицируются через `require_service_token`, не через JWT.
    subject_type = body.get("subject_type")
    if subject_type in ("user", "bot", "oauth_client"):
        identity["actor_type"] = subject_type
    else:
        # Unknown / отсутствующий `subject_type` — фолбэк на `anonymous`,
        # симметрично `audit_outbox._resolve_actor_type`. Раньше тут писали
        # `"user"`: на старом introspect без поля любой PAT/bot/oauth m2m
        # затыкался под `user`, искажая SOC-атрибуцию. Audit с
        # `actor_type="anonymous"` честнее: видно, что introspect не вернул
        # subject_type, и таких событий легко найти grep'ом для миграции
        # стендов. Лог-предупреждение помогает выловить устаревшие
        # auth_service инстансы.
        if subject_type is not None:
            logger.warning(
                "introspect returned unknown subject_type=%r, "
                "falling back to actor_type=anonymous",
                subject_type,
            )
        identity["actor_type"] = "anonymous"

    # Сохраняем ДО role-check'а, чтобы у audit-middleware всегда был actor.
    request.state.auth_identity = identity

    return identity


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """Требует `platform_role=loging_admin`. Полный доступ, включая правила
    и retention."""
    identity = await _fetch_identity(credentials, request)
    if identity.get("platform_role") != "loging_admin":
        raise AppException(
            http_status=403,
            error_code="INSUFFICIENT_ROLE",
            message="platform_role=loging_admin is required to manage loging_service",
        )
    return identity


async def require_reader(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """Read-only доступ. Допустимые роли:

      `loging_admin`    — глобальный read (видит все события);
      `loging_reader`   — глобальный read (видит все события);
      `account_admin`   — глобальный read-only по всему журналу.

    `account_admin` пускается только на чтение (events / stats / export /
    каталог сервисов). Управление правилами и retention остаётся за
    `loging_admin` (`require_admin`) — там `account_admin` получит 403.
    `department_admin` к чтению audit'а по-прежнему не допускается: если
    dep_admin'у нужен read журнала, ему выдаётся отдельная платформенная
    роль `loging_reader` через `auth_service` `POST /users`.

    Dept-scope не применяется: все три допустимые роли — глобальные.
    Эндпоинты, ранее форсившие `identity["_dept_scope"]`, работают cross-dept.
    """
    identity = await _fetch_identity(credentials, request)
    role = identity.get("platform_role")
    if role in ("loging_admin", "loging_reader", "account_admin"):
        return identity

    raise AppException(
        http_status=403,
        error_code="INSUFFICIENT_ROLE",
        message=(
            "Access requires platform_role in (loging_admin, loging_reader, "
            "account_admin). department_admin is not authorised to read the "
            "loging_service audit trail; assign loging_reader explicitly if needed."
        ),
    )


AdminIdentity = Annotated[dict, Depends(require_admin)]
ReaderIdentity = Annotated[dict, Depends(require_reader)]
