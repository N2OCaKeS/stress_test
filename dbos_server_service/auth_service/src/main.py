"""Entrypoint auth_service: создание FastAPI-приложения, middleware, exception handlers."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.router import api_router
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.logging import configure_logging
from src.dependencies.db import get_db
from src.services import audit_context, audit_service
from src.services.audit_context import AuditContext, extract_client_ip
from src.services.audit_events import register_events
from src.services.bootstrap_service import bootstrap_admin

_HEALTH_PATHS = {"/api/auth/v1/health", "/api/auth/v1/ready"}

# Сильные ссылки на startup-таски (`_startup_sequence` в фоне). Без этого
# `asyncio.ensure_future` держит только weakref через loop, GC может собрать
# task до её завершения, и `register_events` молча не доедет до loging_service.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _rate_limit_key_func(request: Request) -> str:
    """slowapi key: реальный client IP с учётом trusted-proxy allow-list.

    Использует тот же `extract_client_ip`, что и audit_context — чтобы
    rate-limit считался для оригинального клиента (а не для k8s-ingress'а),
    но ТОЛЬКО если запрос пришёл с доверенного proxy. Без allow-list'а
    атакующий мог бы подменить XFF и обойти лимит.
    """
    settings = get_settings()
    ip = extract_client_ip(request, list(settings.trusted_proxy_ips or []))
    return ip or "unknown"


def _rate_limit_exceeded_response(request: Request, exc) -> JSONResponse:
    """429-ответ в нашем стандартном envelope формате.

    Shape совместим с `AppException` envelope'ом — UI/clients обрабатывают 429
    как любую другую ошибку. `Retry-After: 60` — стандартный hint для backoff.

    ``exc`` ожидает duck-typed `.detail` атрибут — как у `RateLimitExceeded`,
    так и у нашего in-middleware lightweight-shim'а (см. `rate_limit_middleware`).
    """
    return JSONResponse(
        status_code=429,
        content={
            "error": "too_many_requests",
            "error_code": "RATE_LIMIT_EXCEEDED",
            "message": f"Rate limit exceeded: {exc.detail}",
            "details": {"limit": str(exc.detail)},
            "request_id": getattr(request.state, "request_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        headers={"Retry-After": "60"},
    )


# Module-level Limiter — создан здесь, чтобы он был доступен endpoint-модулям
# через `from src.main import limiter` без circular-import'а. Само навешивание
# лимитов делается через middleware ниже (а НЕ через `@limiter.limit(...)`
# декораторы на endpoint'ы) — это значит, что monkeypatch на `limiter` в
# тестах сразу подхватывается без перерегистрации route'ов.
#
# `key_func` берёт client IP через trusted-proxy allow-list; без allow-list
# X-Forwarded-For игнорится и используется `request.client.host`.
#
# TODO storage_uri (Redis) для prod: дефолтный slowapi-backend — in-memory,
# счётчики локальны для каждого uvicorn-процесса. В K8s с 2+ репликами
# атакующий получает N×limit, потому что round-robin раскладывает попытки
# по pod'ам. Варианты: общий Redis (`Limiter(storage_uri="redis://...")`) или
# sticky sessions на ingress'е. Подробнее — `obsidian/services/auth_service.md`,
# раздел Production hardening.
limiter = Limiter(key_func=_rate_limit_key_func, headers_enabled=True)

# Маршруты с per-endpoint rate-limit'ом. Значения подставляются из
# `Settings.{login,refresh,docker_token}_rate_limit` при сборке приложения.
# Используем full-paths (с префиксом `/api/auth/v1`), т.к. слоу-api проверяется
# в outermost-middleware ДО роутера — у нас нет route_name'а в этот момент.
_RATE_LIMITED_PATHS_FACTORY = {
    "POST /api/auth/v1/login": "login_rate_limit",
    # OAuth2 Password flow (Swagger UI Authorize). Семантически тот же login,
    # должен попадать под тот же лимит чтобы атакующий не обходил `/login`-rate
    # через `/token` form-call'ом.
    "POST /api/auth/v1/token": "login_rate_limit",
    # OAuth2 token endpoint (authorization_code + client_credentials grant).
    # client_credentials проверяет только client_secret через compare_digest,
    # без per-client lockout — без per-IP лимита атакующий с известным
    # client_id перебирает секрет без ограничений. Тот же login-лимит.
    "POST /api/auth/v1/oauth2/token": "login_rate_limit",
    "POST /api/auth/v1/refresh": "refresh_rate_limit",
    "GET /api/auth/v1/docker/token": "docker_token_rate_limit",
    # M2M-call часто, но не безудержно — закрываем от scan/brute по введённым
    # токенам с одного IP.
    "POST /api/auth/v1/authorization/introspect": "introspect_rate_limit",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security headers (HSTS опционально, X-Frame-Options, CSP).

    HSTS включается только при `SECURITY_HSTS_ENABLED=true` — за http-фронтом
    он сломает rebound.
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
        # CSP для JSON-API минимальный — disallow всё, что не нужно. Swagger
        # UI поднимается на /docs (dev-only), у него свой inline-script — но
        # `frame-ancestors 'none'` парный с X-Frame-Options.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        # Permissions-Policy: для JSON-API без UI зануляем sensor-API.
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
        )
        if self._hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            )
        return response


def create_application() -> FastAPI:
    """Собрать FastAPI-приложение. Один раз на процесс — все middleware/handlers wire'аются здесь."""
    settings = get_settings()
    configure_logging(settings.app_log_level)

    _is_prod = settings.app_env == "production"

    # Подготовим лимит-keys для credential-критичных эндпоинтов. Значения
    # читаем из settings (можно override'ить через env LOGIN_RATE_LIMIT/...)
    # и кэшируем здесь как `(method, path) -> "<count>/<period>"` для
    # outermost middleware.
    rate_limited_paths: dict[tuple[str, str], str] = {}
    for route_key, settings_attr in _RATE_LIMITED_PATHS_FACTORY.items():
        method, path = route_key.split(" ", 1)
        rate_limited_paths[(method, path)] = getattr(settings, settings_attr)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup hook:
        - bootstrap admin (если БД пуста);
        - поднять pooled `_audit_client` для emit'а в loging_service;
        - register audit events в фоне.
        """
        async for db in get_db():
            await bootstrap_admin(db)

        # Pooled httpx.AsyncClient для audit-emit в loging_service. Per-call
        # client под burst быстро исчерпает FD-пул (каждый authed запрос →
        # login/refresh/ban/http.client_error audit); pool ставит потолок и
        # амортизирует TLS-handshake. Если `logging_service_url` пуст —
        # `_send_to_logging_service` идёт по per-call fallback'у.
        logging_url = (settings.logging_service_url or "").rstrip("/")
        if logging_url:
            audit_service._audit_client = httpx.AsyncClient(
                base_url=logging_url,
                timeout=2.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )

        # Запуск sequence как task'а В loop'е, а не через asyncio.to_thread:
        # внутри потока emit() ловит RuntimeError на asyncio.get_running_loop()
        # и теряет service.started — наблюдаемо в audit-канале loging_service.
        # Сам register_events использует sync httpx, поэтому внутри sequence
        # он завёрнут в asyncio.to_thread.
        _startup_task = asyncio.create_task(_startup_sequence())
        _BACKGROUND_TASKS.add(_startup_task)
        _startup_task.add_done_callback(_BACKGROUND_TASKS.discard)
        try:
            yield
        finally:
            # Закрываем audit-pool при shutdown. Сначала дожидаемся уже
            # стартовавших emit-тасок (`_EMIT_TASKS`): без drain'а гонка —
            # таска уже выполнила `client = _audit_client` и ушла в await,
            # а shutdown тем временем zeros'ит ссылку и закрывает клиент.
            # Результат — `httpx.ClientClosedError` и потерянное событие.
            # Таймаут 2с симметричен `audit_outbox` drain'у воркера.
            pending = list(audit_service._EMIT_TASKS)
            if pending:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    # Под timeout остаётся часть тасок — потеряем их,
                    # но не подвешиваем shutdown. Лог в audit-канал.
                    pass

            # Обнуляем ссылку только после drain'а. Любые emit'ы, попавшие
            # сюда после этой строки, пойдут per-call fallback'ом.
            audit_pool = audit_service._audit_client
            audit_service._audit_client = None
            if audit_pool is not None:
                await audit_pool.aclose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url=None if _is_prod else "/docs",
        redoc_url=None if _is_prod else "/redoc",
        openapi_url=None if _is_prod else "/openapi.json",
        debug=settings.app_debug,
        swagger_ui_parameters={"persistAuthorization": True},
        lifespan=lifespan,
    )

    # Регистрируем limiter в app.state — slowapi expects `app.state.limiter`.
    # Exception handler возвращает наш стандартный error-envelope с request_id
    # и timestamp (mirrored с server_service).
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_response)

    # ── Security headers + CORS ────────────────────────────────────────────
    # Security headers — на каждом ответе (HSTS только если за https). CORS
    # подключаем только если задан непустой `CORS_ALLOWED_ORIGINS` — иначе
    # сервис закрыт от браузерных вызовов (intent: internal API).
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.security_hsts_enabled,
    )
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    # Регистрируется ПЕРВЫМ из `@app.middleware("http")` → становится innermost
    # (Starlette стакает в обратном порядке), сразу над роутером. Это важно:
    # `audit_access` эмитит http.*-событие ПОСЛЕ возврата из `call_next`, и к
    # этому моменту audit_context должен быть ещё жив. Если бы `audit_access`
    # стоял снаружи `attach_request_id_and_context`, тот успевал бы сбросить
    # контекст в своём finally до emit'а — actor/ip/ua терялись бы.
    @app.middleware("http")
    async def audit_access(request: Request, call_next):
        """Логирование HTTP-ответов 4xx/5xx в audit. Health-paths игнорируются."""
        response = await call_next(request)
        path = request.url.path
        if path in _HEALTH_PATHS:
            return response
        status_code = response.status_code
        if status_code < 400:
            return response

        details = {"method": request.method, "path": path, "status_code": status_code}

        if status_code in (401, 403):
            action, emit_status = "http.access_denied", "denied"
        elif status_code < 500:
            action, emit_status = "http.client_error", "failure"
        else:
            action, emit_status = "http.server_error", "failure"

        # audit_context жив: этот middleware вложен в attach_request_id_and_context,
        # чей finally (reset) сработает только после нашего возврата. Поэтому
        # emit подхватит actor/username/ip/ua/request_id из контекста.
        # Зовём emit напрямую в текущем event-loop'е (НЕ через asyncio.to_thread):
        # в worker-потоке нет running loop'а, emit ловил бы RuntimeError и уходил
        # в sync-fallback без HTTP-доставки — 4xx/5xx не доезжали бы до
        # loging_service. В loop'е emit сам планирует доставку через create_task.
        audit_service.emit(
            action,
            status=emit_status,
            allowed=False,
            details=details,
        )
        return response

    @app.middleware("http")
    async def attach_request_id_and_context(request: Request, call_next):
        """Поставить request_id + audit_context для всего запроса."""
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id

        # Заполняем audit_context из JWT (если есть) и сетевого контекста.
        # `subject_type` берём из JWT claim'а `actor_type`: "user" /
        # "oauth_client" / fallback "user" для старых JWT. Без него все
        # audit-события писались бы `actor_type="user"`, oauth_client-вызовы
        # смешивались бы с человеческими в SIEM.
        actor_id, subject_type = _extract_actor_info(request)
        # X-Forwarded-For доверяется только если запрос пришёл с IP из
        # `settings.trusted_proxy_ips` (CIDR allow-list). Иначе берём прямой
        # `request.client.host`. См. `services/audit_context.py:extract_client_ip`.
        ip = extract_client_ip(request, list(settings.trusted_proxy_ips or []))
        ua = request.headers.get("User-Agent")
        # `username` / `department_id` тут не заполняем — их нет в JWT-payload;
        # `_propagate_identity_to_audit_context` дозаполнит контекст после
        # `get_current_identity` (когда identity resolve'нулась из БД).
        ctx = AuditContext(
            actor_id=actor_id,
            request_id=request_id,
            ip_address=ip,
            user_agent=ua,
            subject_type=subject_type,
        )
        token = audit_context.set_context(ctx)
        try:
            response = await call_next(request)
        finally:
            audit_context.reset_context(token)
        response.headers["X-Request-ID"] = request_id
        return response

    # Распарсенные `RateLimitItem`-объекты per route, один раз при сборке
    # приложения (а не на каждый запрос). Используем `limits.parse(...)`
    # напрямую — это та же библиотека, которую slowapi.Limiter держит внутри.
    from limits import parse as _parse_rate_limit
    parsed_rate_limits: dict[tuple[str, str], "object"] = {
        key: _parse_rate_limit(value) for key, value in rate_limited_paths.items()
    }

    # Per-route per-IP rate-limit (credential-критичные эндпоинты).
    #
    # Регистрируется ПОСЛЕДНИМ из `@app.middleware("http")` → становится
    # outermost (Starlette стакает в обратном порядке). 429-ответ уходит ДО
    # `audit_access`, и rate-limited трафик не порождает
    # `http.client_error`-event'ов — иначе сам limiter подкармливал бы
    # audit-канал и усиливал DoS.
    #
    # Лимит применяется ТОЛЬКО к `(method, path)` парам в `rate_limited_paths`
    # — health/me/groups/users/etc. остаются без per-route лимита.
    # Глобальный per-IP лимит (для всех эндпоинтов) можно добавить отдельным
    # middleware'ом аналогично server_service'у; здесь приоритет — credential
    # surface (login / refresh / docker-token).
    #
    # Под капотом — `limiter.limiter` (это `limits.strategies.FixedWindowRateLimiter`
    # с in-memory storage по умолчанию). Ключ — `<IP>:<METHOD>:<PATH>`, что даёт
    # **независимые** счётчики per (IP, route). Без route'а в ключе один и тот
    # же IP, ломящийся в /login, сжёг бы квоту /refresh.
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """Outermost middleware: проверка per-IP rate-limit'а на critical endpoint'ах."""
        # slowapi-фикс отключён через env (для unit-тестов / devcontainer).
        if not limiter.enabled:
            return await call_next(request)
        key = (request.method, request.url.path)
        item = parsed_rate_limits.get(key)
        if item is None:
            return await call_next(request)
        ip_key = limiter._key_func(request)
        # Per-route ключ: иначе сжигание квоты /login убивало бы /refresh
        # с того же IP. limits-storage хеширует по (namespace, identifier).
        identifier = f"{ip_key}:{request.method}:{request.url.path}"
        hit_allowed = limiter.limiter.hit(item, identifier)
        if not hit_allowed:
            # Имитируем форму `RateLimitExceeded.detail` для
            # `_rate_limit_exceeded_response`. Полноценный
            # `slowapi.errors.RateLimitExceeded` требует `slowapi.wrappers.Limit`
            # с private-полями (key_function/scope/etc.) — для нашего envelope'а
            # достаточно `.detail`. Это локальный класс, не подменяет slowapi'шный
            # exception_handler — последний остаётся на месте для совместимости
            # с прямыми `@limiter.limit(...)` декораторами, если они появятся.
            class _MiniRateLimitExceeded:
                detail = str(item)
            return _rate_limit_exceeded_response(request, _MiniRateLimitExceeded())
        return await call_next(request)

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        """Все доменные исключения мапятся в нашу стандартную JSON-форму.

        `details` пропускаем через `redact()` — если в payload случайно попал
        password/token/hash, наружу уйдёт `<PASSWORD>`/`<TOKEN>`/`<HASH>`, а
        не plaintext. Audit-канал уже redact'ит свой path; здесь зеркалим
        для клиентского ответа.
        """
        from src.services.redaction import redact as _redact
        safe_details = _redact(exc.details) if exc.details else exc.details
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": _http_status_to_category(exc.http_status),
                "error_code": exc.error_code,
                "message": exc.message,
                "details": safe_details,
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Pydantic 422 — приводим к нашей форме (FastAPI default другой)."""
        # `exc.errors()` иногда содержит `ctx` с bytes/objects, которые json не
        # сериализует. Прогоняем через jsonable_encoder с fallback на repr.
        from fastapi.encoders import jsonable_encoder
        safe_errors = jsonable_encoder(
            exc.errors(),
            custom_encoder={bytes: lambda b: b.decode("utf-8", errors="replace")},
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "error_code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": safe_errors},
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    app.include_router(api_router, prefix="/api")

    def custom_openapi():
        """Кастомная OpenAPI-схема — добавляем Bearer и OAuth2Password security schemes для Swagger UI."""
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Вставь access_token, полученный через POST /api/auth/v1/login",
            },
            "OAuth2Password": {
                "type": "oauth2",
                "description": "Логин через username + password (Swagger-форма)",
                "flows": {
                    "password": {
                        "tokenUrl": "/api/auth/v1/token",
                        "scopes": {},
                    }
                },
            },
        }
        schema["security"] = [{"BearerAuth": []}, {"OAuth2Password": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


async def _startup_sequence() -> None:
    """Фоновый запуск после старта: регистрируем audit-события + эмитим service.started.

    Крутится в loop'е, не в worker-потоке: иначе emit() не находит running
    loop и не доставляет service.started в loging_service. Sync httpx внутри
    register_events заворачиваем в asyncio.to_thread, чтобы не блокировать loop.
    """
    await asyncio.to_thread(register_events)
    audit_service.emit("service.started", None, actor_type="service")


def _extract_actor_info(
    request: Request,
) -> tuple[str | None, str | None]:
    """Декодит JWT для actor_id + actor_type (только для аудита).

    Возвращает (actor_id, subject_type). Любая ошибка → (None, None) —
    middleware не валится на битом токене, это работа auth-guard'а.
    `subject_type` берётся из JWT claim `actor_type`; отсутствие → "user"
    (backward compat со старыми JWT).

    `username` и `department_id` намеренно НЕ читаем: их нет в JWT-payload
    (см. `_build_access_token`). Эти поля заполняет
    `_propagate_identity_to_audit_context` после `get_current_identity`.

    Декодированный payload кэшируется в `request.state.jwt_payload` — тот
    же токен потом достанет `get_current_identity` через `_extract_bearer`
    + `decode_access_token`, повторный HS256-decode не нужен.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, None
    token = auth[7:]
    try:
        from src.core.security import decode_access_token
        payload = decode_access_token(token)
        request.state.jwt_payload = payload
        # JWT может не иметь `actor_type` (legacy токены) — fallback на "user",
        # как делает get_current_identity / authorization_service.introspect.
        actor_type = payload.get("actor_type") or "user"
        return payload.get("sub"), actor_type
    except Exception:
        return None, None


def _http_status_to_category(status: int) -> str:
    """HTTP-статус → стабильное короткое категорийное имя (для поля `error` в ответе)."""
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
