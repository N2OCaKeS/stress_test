"""Application entrypoint loging_service'а."""

import asyncio
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.logging import configure_logging
from src.dependencies import auth as auth_deps


# Pending fire-and-forget self-audit задачи. Под SIGTERM lifespan drain'ит
# их с коротким timeout'ом, иначе последние http.* events терялись
# (`asyncio.ensure_future` без ref'а собирает GC, если loop закрывается
# до их выполнения).
_pending_audit_tasks: set[asyncio.Task] = set()
_AUDIT_DRAIN_TIMEOUT_SECONDS = 2.0


# Strict-numeric content-length: `int()` принимает `+1`, `_`-сепараторы,
# окружающие пробелы и юникодные digits — всё это потенциальные smuggling
# поверхности на стеке без h11 0.16 (или прокси, нормализующего header).
# RFC 9110 разрешает только ASCII `0-9`. fullmatch гарантирует, что после
# strip'а строка состоит из этих цифр и ничего больше.
_CONTENT_LENGTH_RE = re.compile(r"\d+")


# Пути, которые НИКОГДА не должны попадать под rate-limit:
#   * `/health` и `/ready` — Kubernetes-пробы; throttling их флапает под;
#   * `/token` — login администратора; troughtled на стороне auth_service.
# Этот guard читает `_rate_limit_key`, который короткозамыкается в
# unique-per-request ключ для этих путей; вместе с per-key counter slowapi
# это делает их фактически unlimited.
_RATE_LIMIT_EXEMPT_PATHS = {
    "/api/logging/v1/health",
    "/api/logging/v1/ready",
    "/api/logging/v1/token",
}


def _rate_limit_key(request: Request) -> str:
    """Дефолтная key-функция SlowAPI-лимитера.

    Для health/token возвращает uuid-based unique-ключ, чтобы они не копили
    counts ни в каком общем bucket'е. Для всего остального — client IP.
    Ingest-канал (`POST /events`) и batch-канал
    (`POST /services/{service}/events`) переопределяют key_func на
    per-service-identity (`X-Service-Identity`) прямо в декораторах — за k8s
    ingress общий per-IP bucket позволил бы одному сервису выжать бюджет
    остальных.
    """
    if request.url.path in _RATE_LIMIT_EXEMPT_PATHS:
        # Unique-ключ на каждый запрос → никогда не коллизит с чужим bucket'ом.
        return f"exempt:{uuid.uuid4().hex}"
    return get_remote_address(request)


# Module-level limiter, чтобы декораторы (`@limiter.limit(...)`) могли на него
# ссылаться. `app.state.limiter` должен указывать на этот же инстанс, иначе
# middleware его не найдёт.
#
# ВАЖНО: эта строка ОБЯЗАНА быть ДО `from src.api.router import api_router`
# ниже, потому что `endpoints/events.py` делает `from src.main import
# limiter` для прикрутки декоратора. Без этого порядка Python увидит
# полу-инициализированный `src.main` без `limiter`-атрибута и поднимет
# `ImportError`.
#
# `headers_enabled` по умолчанию off — `X-RateLimit-Remaining` утекает
# атакующему live feedback его rate-counter'а, и тот burst'ит под лимит,
# не словив 429. Включается `RATE_LIMIT_HEADERS_ENABLED=true` для отладки.
# Дефолт slowapi-Limiter'а тоже `False`, но явное чтение из settings даёт
# хук под
# env-override был очевидным.
limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[],  # применяются только явные `@limiter.limit` декораторы
    headers_enabled=get_settings().rate_limit_headers_enabled,
)

from src.api.router import api_router  # noqa: E402 — см. ordering note выше


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security headers (HSTS опционально, X-Frame-Options, CSP).

    HSTS включается только при `SECURITY_HSTS_ENABLED=true` — за http-фронтом
    он сломает rebound. Зеркалит auth_service.
    """

    def __init__(self, app, *, hsts_enabled: bool):
        super().__init__(app)
        self._hsts_enabled = hsts_enabled

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        # CSP для JSON-API минимальный — disallow всё лишнее. Swagger UI
        # на /docs (dev-only) тянет свои inline-скрипты, но `frame-ancestors
        # 'none'` парный с X-Frame-Options прикрывает clickjacking.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        if self._hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            )
        return response


def create_application() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_log_level)

    _is_prod = settings.app_env == "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Долгоживущие ресурсы: pooled introspect-клиент + retention-цикл.

        Startup:
          * Подпуливаем один `httpx.AsyncClient` для `_fetch_identity` →
            auth_service. Bounded limits защищают от slowloris-amplification,
            когда N параллельных reader-запросов иначе открывали бы N свежих
            TCP+TLS handshake'ов (как в `server_service`). Скипаем, если
            `AUTH_SERVICE_URL` не задан (early-dev конфиги); `_fetch_identity`
            тогда фоллбэчится на per-call `httpx.post`.
          * Стартуем retention-cleanup daemon-thread (раньше висел на
            `@app.on_event("startup")`, теперь свёрнут в lifespan, чтобы
            убрать FastAPI DeprecationWarning). Под `RETENTION_LOOP_ENABLED=False`
            пропускается — нужно тестам, у которых module-level `SessionLocal`
            всё равно смотрит мимо TEST_DATABASE_URL.

        Shutdown:
          * Закрываем pooled-клиент (`await aclose()`). Retention-тред —
            daemon, умирает вместе с процессом.
        """
        # `settings` снаружи — снимок на момент `create_application()`. Тесты
        # дёргают `monkeypatch.setenv(...)` + `get_settings.cache_clear()`
        # между импортом модуля и стартом lifespan'а, поэтому здесь
        # перечитываем актуальный `get_settings()`. Без этого тестовые
        # переопределения (`AUTH_SERVICE_URL`, `RETENTION_LOOP_ENABLED`)
        # игнорируются.
        live_settings = get_settings()
        url = live_settings.auth_service_url
        if url:
            auth_deps._introspect_client = httpx.AsyncClient(
                base_url=url.rstrip("/"),
                timeout=httpx.Timeout(
                    live_settings.introspect_timeout_seconds, connect=2.0
                ),
                limits=httpx.Limits(
                    max_connections=20, max_keepalive_connections=10
                ),
                verify=live_settings.introspect_tls_verify,
            )
        if live_settings.retention_loop_enabled:
            # Retention-цикл — daemon thread, как и старый on_event hook.
            t = threading.Thread(target=_retention_loop, daemon=True)
            t.start()
        try:
            yield
        finally:
            # Drain pending self-audit задачи с timeout'ом. Snapshot'им set
            # потому что `add_done_callback(discard)` мутирует его во время
            # gather'а.
            pending = list(_pending_audit_tasks)
            if pending:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=_AUDIT_DRAIN_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "audit drain timed out after %.1fs, %d tasks dropped",
                        _AUDIT_DRAIN_TIMEOUT_SECONDS,
                        sum(1 for t in pending if not t.done()),
                    )
            client = auth_deps._introspect_client
            auth_deps._introspect_client = None
            if client is not None:
                await client.aclose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # В проде Swagger/OpenAPI скрыты: persistAuthorization кладёт
        # SERVICE_API_KEY в localStorage браузера, а сам OpenAPI раскрывает
        # схемы и error-codes. Зеркалит auth_service.
        docs_url=None if _is_prod else "/docs",
        redoc_url=None if _is_prod else "/redoc",
        openapi_url=None if _is_prod else "/openapi.json",
        debug=settings.app_debug,
        swagger_ui_parameters={"persistAuthorization": True},
        lifespan=lifespan,
    )

    # Per-IP rate-limit wiring (slowapi, decorator-only mode):
    #   * `app.state.limiter` держит slowapi-машинерию introspectable
    #     (admin-эндпоинты, тесты) и совпадает с официальным wiring-паттерном.
    #   * Сам limit-check живёт на `@limiter.limit` декораторе в
    #     `endpoints/events.py` — slowapi `Limiter._check_request_limit`
    #     вызывает `limiter.hit()` ровно один раз на запрос.
    #   * НАМЕРЕННО не регистрируем `SlowAPIMiddleware` здесь:
    #     middleware + декоратор вместе double-count'ят hits (middleware не
    #     ставит `request.state._rate_limiting_complete` перед делегацией
    #     в route, так что обёртка декоратора пере-проверяет тот же лимит
    #     и инкрементит counter второй раз — подтверждено трассировкой
    #     `slowapi/middleware.py:43` vs `extension.py:730`).
    #   * `RateLimitExceeded`, поднятый декоратором, ловится
    #     `@app.exception_handler(RateLimitExceeded)` ниже.
    app.state.limiter = limiter

    _HEALTH_PATHS = {"/api/logging/v1/health", "/api/logging/v1/ready"}

    # ВАЖНО про порядок Starlette `@app.middleware("http")` — LIFO:
    # последний зарегистрированный = outermost. Чтобы получить стек
    # outer → inner: `limit_body_size → audit_access → attach_request_id → route`,
    # регистрируем в обратном порядке:
    #   1. `attach_request_id`  — innermost (видит response от route);
    #   2. `audit_access`       — посередине (эмитит audit на 4xx/5xx);
    #   3. `limit_body_size`    — outermost (рубит 413 первым).
    #
    # Зачем body-limit outermost:
    #   * 413-ответ возвращается ДО `audit_access` → нет audit-emit'а на
    #     отбитом трафике (зеркалит 429-amplification фикс в `server_service`);
    #   * `Content-Length` проверяется ДО того, как ASGI начнёт читать body
    #     в память — единственный момент, когда можно дёшево отбить
    #     многомегабайтный запрос. Если пропустить дальше, body будет
    #     прочитан полностью (включая pydantic-парсинг) и pool/память уже
    #     расходованы.
    #   * Rate-limit на `POST /events` — slowapi-декоратор внутри route, то
    #     есть ещё глубже attach_request_id → 413 от body-limit срабатывает
    #     и до rate-limit'а тоже (что и должно быть: лишний incoming-counter
    #     тратится только на правильно сформированные запросы под лимитом).

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        # Санитизация incoming `X-Request-ID` — defence-in-depth от
        # response-splitting. `EventCreate.request_id` валидирует тот же
        # charset, но middleware видит header на КАЖДОМ запросе (health,
        # token, admin), не только на десериализованных `EventCreate`.
        # Cheap path: убираем CR/LF/NUL (splittable header-терминаторы),
        # cap 64 символа, пустое после strip'а → автогенерируемый id.
        raw_request_id = request.headers.get("X-Request-ID")
        request_id: str | None = None
        if raw_request_id is not None:
            sanitized = (
                raw_request_id.replace("\r", "")
                .replace("\n", "")
                .replace("\x00", "")
                .strip()[:64]
            )
            if sanitized:
                request_id = sanitized
        if request_id is None:
            request_id = f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # Эндпоинты, где аудит не нужен (служебные / рекурсивные).
    _SKIP_AUDIT_PATHS = {
        "/api/logging/v1/health",
        "/api/logging/v1/ready",
        "/api/logging/v1/token",
    }
    # POST на эти префиксы — входящие от сервисов, не аудитируем (избегаем
    # рекурсии: события приёма событий).
    _INGEST_PREFIXES = (
        "/api/logging/v1/events",
        "/api/logging/v1/services/",  # POST /services/{svc}/events
    )

    @app.middleware("http")
    async def audit_access(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        status_code = response.status_code

        # Скипаем служебные пути.
        if path in _SKIP_AUDIT_PATHS:
            return response

        # POST от сервисов (ingest событий, регистрация событий): успешный
        # приём НЕ аудитируем — это осознанный anti-amplification (loging не
        # должен писать audit-событие на каждое принятое событие, иначе
        # рекурсия/усиление). Но auth-провалы (401/403) на этих путях
        # пропускать нельзя: иначе перебор SERVICE_API_KEY не оставляет следа
        # ни в журнале, ни у SOC. Проваленный ingest аудитируется ниже как
        # обычный http.access_denied (actor_id=None), не как ingest-событие —
        # петли не создаёт.
        if request.method == "POST" and path.startswith(_INGEST_PREFIXES):
            if status_code not in (401, 403):
                return response

        identity = getattr(request.state, "auth_identity", None)
        actor_id = identity.get("user_id") if identity else None
        username = identity.get("username") if identity else None
        # `actor_type` пробрасывание: `_fetch_identity` кладёт `subject_type`
        # от auth_service introspect ("user"/"bot"/"oauth_client") на
        # identity-dict. Без этого M2M caller'ы (PAT/bot/oauth_client)
        # писались бы в audit как "user" — fake user-активность для SOC.
        actor_type = identity.get("actor_type") if identity else None
        request_id = getattr(request.state, "request_id", None)
        details: dict = {"method": request.method, "path": path, "status_code": status_code}
        if identity and identity.get("platform_role"):
            details["platform_role"] = identity["platform_role"]

        if status_code in (401, 403):
            action, emit_status, allowed = "http.access_denied", "denied", False
        elif status_code >= 500:
            action, emit_status, allowed = "http.server_error", "failure", False
        elif status_code >= 400:
            action, emit_status, allowed = "http.client_error", "failure", False
        else:
            action, emit_status, allowed = _action_for_path(request.method, path), "success", True

        task = asyncio.ensure_future(asyncio.to_thread(
            _emit_audit, action, actor_id, actor_type, username, emit_status, allowed, request_id, details
        ))
        # Держим ref до завершения — иначе GC может собрать task до того,
        # как `to_thread` отработает. Discard на done — set не растёт.
        _pending_audit_tasks.add(task)
        task.add_done_callback(_pending_audit_tasks.discard)
        return response

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        """Отбивает мутирующие запросы с body больше `max_request_body_bytes`.

        DoS-фикс (нет лимита размера body + pool=30 → DoS):
        `EventCreate._details_size` кэпает только под-dict `details` в 64 KiB
        ПОСЛЕ того, как pydantic уже распарсил весь body. Один атакующий
        с `SERVICE_API_KEY` может прислать 100 MB body — ASGI читает его в
        память до того, как стартует любой валидатор, и 30 параллельных
        uploads × 100 MB ≈ 3 GB RSS плюс pgsql-pool насыщается ещё до
        того, как один client упрётся в per-IP rate-limit slowapi
        (default 100/min).

        Стратегия:
        * Cheap path — доверяем `Content-Length`, если есть. Стандарт-compliant
          HTTP/1.1 и HTTP/2 клиенты всегда шлют его на fixed-size body; ASGI
          передаёт header без изменений. Если declared length больше cap'а —
          возвращаем 413 сразу, не трогая body.
        * Если `Content-Length` нет (chunked transfer-encoding) или его не
          распарсить, мы НЕ читаем body проактивно и не считаем байты в
          middleware — это потребовало бы буферизации всего body внутри
          middleware, что defeats the purpose. Пропускаем дальше;
          defence-in-depth идёт от: (a) uvicorn'овского
          `--limit-concurrency` флага в деплое, (b) per-field length caps
          в `EventCreate`, (c) per-IP slowapi rate-limit. Follow-up в TODO
          отчёта.

        Проверяем только `POST/PUT/PATCH` — `GET/HEAD/DELETE/OPTIONS`
        body для audit-ingest не несут.
        """
        if request.method in ("POST", "PUT", "PATCH"):
            settings = get_settings()
            max_size = settings.max_request_body_bytes
            cl = request.headers.get("content-length")
            if cl is not None:
                # `int(cl)` принимал `+50000000`, `1_000_000`, окружающие
                # пробелы и `int("-1")` (последнее ловилось отдельным
                # негативным гардом). На стеке без h11 0.16 — или за
                # прокси, которое нормализует header перед передачей —
                # `+` и пробелы могли вернуться в downstream-парсер уже
                # как валидное число, открывая body-smuggling. Strict
                # fullmatch `\d+` после strip'а покрывает все варианты:
                # отрицательные, плюс, underscores, whitespace, юникодные
                # digits — всё отбивается единым 400.
                stripped = cl.strip()
                if not _CONTENT_LENGTH_RE.fullmatch(stripped):
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": "bad_request",
                            "error_code": "INVALID_CONTENT_LENGTH",
                            "message": "Content-Length header is malformed",
                            "details": {},
                            "request_id": None,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                declared = int(stripped)
                if declared > max_size:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": "payload_too_large",
                            "error_code": "PAYLOAD_TOO_LARGE",
                            "message": (
                                f"Request body exceeds the {max_size}-byte limit"
                            ),
                            "details": {
                                "max_bytes": max_size,
                                "declared_bytes": declared,
                            },
                            "request_id": None,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
        return await call_next(request)

    # SecurityHeadersMiddleware регистрируем последним → outermost слой.
    # Так заголовки попадают на КАЖДЫЙ ответ, включая 413 от
    # `limit_body_size` и 429 от rate-limit, а не только на route-ответы.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.security_hsts_enabled,
    )

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": _http_status_to_category(exc.http_status),
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
        # Держим стандартный error envelope (как у AppException), чтобы
        # клиенты диспатчили по `error_code`, а не парсили plain-text body
        # дефолтного slowapi. `exc.detail` несёт лимит, который сработал
        # (например, "100 per 1 minute").
        return JSONResponse(
            status_code=429,
            content={
                "error": "too_many_requests",
                "error_code": "RATE_LIMIT_EXCEEDED",
                "message": f"Ingest rate limit exceeded: {exc.detail}",
                "details": {"limit": str(exc.detail)},
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Retry-After": "60"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "error_code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": jsonable_encoder(exc.errors())},
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    app.include_router(api_router, prefix="/api")

    # NB: `start_retention_cleanup` раньше был `@app.on_event("startup")`
    # хуком здесь; свёрнут в `lifespan` выше, чтобы убрать
    # DeprecationWarning и держать порядок startup'а (introspect-pool
    # первый, потом daemon-thread) явным в одном месте.

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)

        # Заменяем auto-сгенерированный HTTPBearer на наши именованные схемы.
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes.pop("HTTPBearer", None)
        schemes["ServiceAPIKey"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Вставь SERVICE_API_KEY как есть (service-to-service auth)",
        }
        schemes["OAuth2Password"] = {
            "type": "oauth2",
            "description": "Логин по LOGGING_ADMIN_USERNAME + LOGGING_ADMIN_PASSWORD",
            "flows": {
                "password": {
                    "tokenUrl": "/api/logging/v1/token",
                    "scopes": {},
                }
            },
        }

        # Подменяем endpoint-level HTTPBearer-ссылки, чтобы принимались оба flow'а.
        _AUTH = [{"ServiceAPIKey": []}, {"OAuth2Password": []}]
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict) and "security" in operation:
                    operation["security"] = _AUTH

        schema["security"] = _AUTH
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


# Стабильный 64-bit ключ для `pg_advisory_lock`. Hash литерала
# `"loging_service.retention"` — `pg_try_advisory_lock(bigint)` ждёт
# именно signed bigint. Под multi-replica только один держатель ключа
# выполняет cleanup; остальные пропускают итерацию.
#
# Конкретное число — `int.from_bytes(b"loretent", "big")` (8 байт ASCII),
# фиксировано чтобы все replica'ы и человек-debugger могли проверить lock
# в `pg_locks` по одному и тому же значению.
_RETENTION_ADVISORY_LOCK_KEY: int = int.from_bytes(b"loretent", "big")


def _retention_loop() -> None:
    """Фоновый тред: применяет retention-политику раз в сутки в 00:00 MSK (UTC+3).

    Тик минутный, через `sleep_until(next_run)` — устраняем drift, когда
    под нагрузкой `time.sleep(60)` undershoot'ил и тик мог проспать минуту
    или, наоборот, добавлять накопленную задержку на каждой итерации.

    Под multi-replica каждый instance тикает независимо. Чтобы DELETE не
    шёл из N replica'ов одновременно (DELETE race + WAL amplification),
    apply-проход обёрнут в `pg_try_advisory_lock` — только держатель
    lock'а делает работу, остальные тихо пропускают.
    """
    from datetime import date
    from zoneinfo import ZoneInfo
    from sqlalchemy import text
    _MSK = ZoneInfo("Europe/Moscow")
    last_run: date | None = None

    # Базовый момент tick'а — выровнен на минуту. `next_run` всегда в будущем
    # на N*60s; sleep до него убирает накопление drift'а.
    next_run = time.monotonic() + 60.0

    while True:
        now_msk = datetime.now(_MSK)
        today = now_msk.date()

        if last_run != today and now_msk.hour == 0:
            try:
                from src.db.session import SessionLocal
                from src.repositories.retention_policies import apply_active
                db = SessionLocal()
                try:
                    locked = db.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": _RETENTION_ADVISORY_LOCK_KEY},
                    ).scalar()
                    if not locked:
                        # Lock держит другая replica — она и сделает cleanup.
                        # Мы всё равно фиксируем last_run, чтобы не ретраить
                        # в каждый последующий минутный tick этого часа.
                        logger.info(
                            "Retention cleanup [%s MSK]: skipped, advisory lock held by another replica",
                            today,
                        )
                        last_run = today
                    else:
                        try:
                            deleted = apply_active(db)
                            logger.info(
                                "Retention cleanup [%s MSK]: deleted %d events",
                                today, deleted,
                            )
                            # Self-audit факта sweep'а: compliance требует
                            # запись «событие удалено». Берётся фактический
                            # `retain_days` активной политики (если её нет —
                            # apply_active вернул 0 без работы).
                            try:
                                from src.repositories.retention_policies import get_active
                                from src.schemas.events import EventCreate
                                from src.services.event_service import record_admin_action
                                policy = get_active(db)
                                retain_days = policy.retain_days if policy else None
                                record_admin_action(
                                    db,
                                    EventCreate(
                                        timestamp=datetime.now(timezone.utc),
                                        service="loging_service",
                                        action="logging.retention_sweep",
                                        actor_id=None,
                                        actor_type="service",
                                        username="loging_service",
                                        status="success",
                                        allowed=True,
                                        severity=None,
                                        details={
                                            "deleted_count": int(deleted),
                                            "retain_days": retain_days,
                                            "run_date_msk": today.isoformat(),
                                        },
                                    ),
                                )
                            except Exception as audit_exc:
                                # Sweep сам прошёл успешно — теряем только audit.
                                # Логируем, но не валим last_run.
                                logger.error(
                                    "retention sweep self-audit failed: %s",
                                    audit_exc,
                                    exc_info=True,
                                )
                            last_run = today
                        finally:
                            db.execute(
                                text("SELECT pg_advisory_unlock(:k)"),
                                {"k": _RETENTION_ADVISORY_LOCK_KEY},
                            )
                            db.commit()
                finally:
                    db.close()
            except Exception as exc:
                logger.error("Retention cleanup failed: %s", exc)

        # Sleep до следующего минутного tick'а, не на 60s от текущего момента.
        # Если cleanup занял 25s, спим только 35s до 60s-границы. Если по
        # какой-то причине мы проспали (next_run уже в прошлом) — следующий
        # tick немедленный.
        now = time.monotonic()
        sleep_seconds = next_run - now
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        next_run += 60.0
        # Защита от unbounded catch-up при долгой паузе процесса
        # (suspend → resume через час). Иначе цикл крутил бы 60 итераций
        # без сна. Pin'им next_run к now + 60.
        if next_run < time.monotonic():
            next_run = time.monotonic() + 60.0


def _action_for_path(method: str, path: str) -> str:
    """Возвращает имя action для успешного обращения к admin-эндпоинту loging_service."""
    if "/rules" in path:
        return "logging.rules_read" if method == "GET" else "logging.rules_write"
    if "/services" in path:
        return "logging.services_read"
    if "/events" in path:
        return "logging.events_queried"
    return "logging.admin_access"


# Defence-in-depth: counter тихих self-audit ошибок. Без него silent failure
# маскирует регрессии (БД упала, schema mismatch, AppException от
# `record_admin_action`). Когда в loging_service появится Prometheus-клиент,
# эту переменную заменит `Counter("loging_self_audit_failures_total", ...)`.
self_audit_failures_total = 0


_VALID_ACTOR_TYPES = frozenset({"user", "bot", "service", "anonymous", "oauth_client"})


def _emit_audit(
    action: str,
    actor_id: str | None,
    actor_type: str | None,
    username: str | None,
    emit_status: str,
    allowed: bool,
    request_id: str | None,
    details: dict,
) -> None:
    """Пишет событие loging_service напрямую в БД — всегда минуя SUPPRESS-правила.

    Это гарантирует, что аудит loging_service нельзя скрыть никакими
    правилами.

    Ошибки записи логируются (`logger.error` + counter) — silent failure
    антипаттерн: маскирует регрессии инварианта «self-audit всегда пишется».

    `actor_type` пробрасывается из identity (через `_fetch_identity` —
    маппинг от auth_service introspect `subject_type`). Значения
    `"user"`/`"bot"`/`"oauth_client"`/`"service"` идут как есть; None или
    unknown → `"user"`, если `actor_id` есть, иначе `"anonymous"`. Без
    этого PAT/bot/oauth_client писались бы в audit как fake-user.
    """
    global self_audit_failures_total

    # Резолвим actor_type c backward-compat fallback'ом. `_VALID_ACTOR_TYPES`
    # совпадает с `EventCreate.actor_type` Literal whitelist'ом.
    if actor_type in _VALID_ACTOR_TYPES:
        resolved_actor_type = actor_type
    else:
        resolved_actor_type = "user" if actor_id else "anonymous"

    from src.db.session import SessionLocal
    from src.schemas.events import EventCreate
    from src.services.event_service import record_admin_action
    db = SessionLocal()
    try:
        record_admin_action(
            db,
            EventCreate(
                timestamp=datetime.now(timezone.utc),
                service="loging_service",
                action=action,
                actor_id=actor_id,
                actor_type=resolved_actor_type,
                username=username,
                status=emit_status,
                allowed=allowed,
                request_id=request_id,
                details=details,
            ),
        )
    except Exception as exc:
        self_audit_failures_total += 1
        logger.error("self-audit failed: %s", exc, exc_info=True)
    finally:
        db.close()


def _http_status_to_category(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "too_many_requests",
        503: "service_unavailable",
    }.get(status, "internal_error")


app = create_application()
