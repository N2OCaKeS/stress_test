"""Entrypoint приложения server_service. FastAPI app + middleware + lifespan."""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.router import api_router
from src.core.config import get_settings
from src.core.limiter import limiter
from src.core.exceptions import AppException
from src.dependencies import auth as auth_deps
from src.middleware.https_guard import HTTPSRequiredMiddleware
from src.middleware.platform_admin_guard import platform_admin_guard
from src.services import audit_context, audit_service, worker_client
from src.services.audit_context import AuditContext
from src.services.audit_events import register_events

# Полный таймаут (сек) на синхронную startup-audit-последовательность
# (`register_events()` + `service.started` emit). При превышении — startup
# продолжается, в лог уходит WARNING. Держим ниже k8s readiness
# `initialDelaySeconds`, чтобы helm-chart успел стартануть под ним.
_STARTUP_AUDIT_TIMEOUT_SECONDS = 10.0

# Сколько секунд ждём pending audit-emit'ов на shutdown.
# `emit()` шедулит `loop.create_task(_send_to_logging_service(...))`; если
# закроем `_audit_client` до их завершения — event'ы теряются с
# `httpx.ClientClosedError`. 2с — баланс: хватает медленному loging_service,
# но укладывается в k8s terminationGracePeriodSeconds (~30s).
_AUDIT_DRAIN_TIMEOUT_SECONDS = 2.0

logger = logging.getLogger("server_service.startup")

# Health-paths — middleware пропускает их без аудита и без rate-limit'а.
# server_service вешает health/ready на /api/server/v1/health и /api/server/v1/ready.
_HEALTH_PATHS = {"/api/server/v1/health", "/api/server/v1/ready"}


def _is_health_path(path: str) -> bool:
    """True если path относится к health/ready (исключён из rate-limit'а)."""
    return path in _HEALTH_PATHS


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security headers (HSTS опционально, X-Frame-Options, CSP).

    HSTS включается только при `SECURITY_HSTS_ENABLED=true` — за http-фронтом
    он сломает rebound. Зеркалит auth_service / loging_service.
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
        # CSP для JSON-API минимальный — disallow всё лишнее. Swagger UI на
        # /docs (dev-only) тянет свои inline-скрипты, но `frame-ancestors
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


def _rate_limit_exceeded_response(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Кастомный 429-ответ, совместимый по shape с `AppException`."""
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


def create_application() -> FastAPI:
    """Собрать FastAPI app: lifespan + middleware + exception handlers + роутер."""
    settings = get_settings()

    # Slowloris-защита часть (b): per-IP rate-limit на 401-pipeline (и любой
    # другой). Bearer-shape pre-check (часть c) отбивает заведомо невалидные
    # токены без RTT в auth_service, introspect-pool (часть a) ограничивает
    # outbound-нагрузку — но без rate-limit'а атакующий с пулом IP может
    # бомбить CPU и заполнять audit-канал shape-valid (но ban'нутыми)
    # токенами. slowapi применяется ДО endpoint-handler'а и эмитит 429,
    # минуя introspect и БД.
    #
    # Health endpoints (/health, /ready) исключены из лимита — k8s probe
    # не должен натыкаться на 429 при тестовой нагрузке. Исключение —
    # в `rate_limit_middleware` ниже (path-bypass до `_check_request_limit`).
    #
    # Сам limiter живёт в `src.core.limiter` — endpoint-модули вешают
    # `@limiter.limit(...)` декораторы (для os_versions anon, ipmi rotate
    # и т.п.), и через нейтральный модуль не образуется цикл импорта.

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Поднимаем pooled httpx.AsyncClient для introspect (slowloris-фикс).
        # Bounded limits: на slowloris-burst пул не разрастается, ставит
        # back-pressure на дальнейшие introspect-запросы. Размер пула
        # конфигурируется через AUTH_POOL_MAX_CONNECTIONS /
        # AUTH_POOL_MAX_KEEPALIVE (или legacy INTROSPECT_POOL_*), чтобы
        # прод можно было тюнить под нагрузку без правки кода.
        pool_limits = httpx.Limits(
            max_connections=settings.introspect_pool_max_connections,
            max_keepalive_connections=settings.introspect_pool_max_keepalive,
        )
        auth_deps._introspect_client = httpx.AsyncClient(
            base_url=settings.auth_service_url.rstrip("/"),
            timeout=settings.auth_request_timeout_seconds,
            limits=pool_limits,
        )
        # Pooled client для audit-emit в loging_service. Каждый authenticated
        # request может породить audit-emission (grant/ban/power/http.client_error
        # в middleware), per-call client → FD-amplification под slowloris.
        # base_url берётся из settings; если пусто — оставляем None и
        # `_send_to_logging_service` идёт по fallback per-call.
        logging_url = (settings.logging_service_url or "").rstrip("/")
        if logging_url:
            audit_service._audit_client = httpx.AsyncClient(
                base_url=logging_url,
                timeout=2.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        # Pooled aioredis-клиент для bootstrap-кред prepare'а. До этого
        # `store_prepare_creds` строил `aioredis.from_url(...)` per-call —
        # burst POST /prepare ронял Redis на connection-budget. Если
        # `server_worker_redis_url` не задан, пул не создаём — сам
        # `store_prepare_creds` поднимет WORKER_REDIS_NOT_CONFIGURED.
        if settings.server_worker_redis_url:
            worker_client._prepare_redis_client = aioredis.from_url(
                settings.server_worker_redis_url,
            )
        # Регистрируем event-каталог в loging_service + эмитим `service.started`
        # СИНХРОННО на startup (await до yield). Это закрывает прежнюю
        # race-condition с `asyncio.ensure_future(asyncio.to_thread(...))` —
        # fire-and-forget task мог потеряться при холодном старте до того, как
        # to_thread успевал запланироваться, и `service.started` оставался
        # неотправленным. Сейчас обе операции выполняются в running event-loop
        # с явным timeout — при timeout / сбое loging_service startup
        # продолжается (WARNING-лог), но per-API таймаут не висит бесконечно.
        try:
            await asyncio.wait_for(
                _run_startup_audit_sequence(),
                timeout=_STARTUP_AUDIT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "startup audit sequence timed out after %.1fs — "
                "service.started may be missing in loging_service audit log",
                _STARTUP_AUDIT_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort: не блокируем startup
            logger.warning(
                "startup audit sequence failed (%s: %s) — "
                "service.started may be missing in loging_service audit log",
                type(exc).__name__,
                exc,
            )
        try:
            yield
        finally:
            # Shutdown order:
            #
            #   1. `introspect_client` — закрываем ПЕРВЫМИ. После закрытия
            #      любая дальнейшая попытка introspect-call'а упадёт с
            #      `RuntimeError("Cannot send a request, ...")`. Это ок:
            #      uvicorn graceful shutdown к этому моменту уже остановил приём
            #      новых HTTP-запросов, in-flight в endpoint'ах либо завершились,
            #      либо упадут с понятной ошибкой и нам не страшно.
            #   2. **Drain pending audit-tasks** — `audit_service.emit()` шедулит
            #      fire-and-forget через `loop.create_task(_send_to_logging_service(...))`,
            #      эти task'и держат ссылку на pooled `_audit_client`. Если
            #      закрыть `_audit_client` до того, как они отправились — task'и
            #      падают в `httpx.ClientClosedError`, audit-event теряется.
            #      Сбор pending task'ов через `asyncio.all_tasks(loop)` минус
            #      текущий task — даём им финальный chance дойти до сети
            #      (`asyncio.wait` с `_AUDIT_DRAIN_TIMEOUT`).
            #   3. `_audit_client` — закрываем ПОСЛЕДНИМ, после drain'а. Любой
            #      audit-emit, шедулящийся уже после этой точки (rare race —
            #      теоретически от late-completing finally-блока endpoint'а),
            #      попадёт в `client is None` ветку → sync-fallback per-call
            #      httpx.post с timeout=2.0 (без leak'а pooled-сокетов).
            introspect = auth_deps._introspect_client
            auth_deps._introspect_client = None
            if introspect is not None:
                await introspect.aclose()

            # Drain in-flight audit emit'ов: pooled `_audit_client` ещё жив,
            # task'и должны успеть отправиться до его закрытия.
            await _drain_pending_audit_tasks()

            audit_pool = audit_service._audit_client
            audit_service._audit_client = None
            if audit_pool is not None:
                await audit_pool.aclose()

            # Pooled prepare-creds Redis-клиент закрываем последним: с этого
            # момента входящих POST /prepare уже нет (uvicorn graceful drain
            # отработал выше), а `_prepare_redis_client = None` сбрасывает
            # модульный slot, чтобы повторный запуск lifespan (в тестах через
            # `app.router.lifespan_context`) не наследовал закрытый client.
            prepare_redis = worker_client._prepare_redis_client
            worker_client._prepare_redis_client = None
            if prepare_redis is not None:
                await prepare_redis.aclose()

            # taskiq-broker держит свой Redis-pool — закрываем его и
            # сбрасываем модульные `_worker_broker`/`_broker_started`, чтобы
            # повторный lifespan (в тестах) поднял свежий broker, а не
            # реиспользовал закрытый.
            await worker_client.shutdown_broker()

    # В production закрываем публичный OpenAPI/Swagger UI — анонимы не должны
    # видеть каталог эндпоинтов (включая stub-501 с summary вроде «Reveal decrypted
    # IPMI credentials»). В dev/test/local остаётся открытым для разработки.
    is_production = settings.app_env.lower() == "production"
    openapi_url = None if is_production else "/openapi.json"
    docs_url = None if is_production else "/docs"
    redoc_url = None if is_production else "/redoc"

    app = FastAPI(
        title="server_service",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        swagger_ui_parameters={"persistAuthorization": True},
        lifespan=lifespan,
    )

    # Регистрируем limiter в app.state — slowapi.SlowAPIMiddleware / декораторы
    # ищут его именно там. Exception handler возвращает наш стандартный
    # error-envelope с request_id и timestamp.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_response)

    # ВАЖНО: Starlette стакает `@app.middleware("http")` в **обратном** порядке
    # регистрации — последний зарегистрированный = outermost. Чтобы достичь
    # порядка outer→inner: `SecurityHeaders → rate_limit → attach_request_id → platform_admin_guard → audit_access → route`,
    # регистрируем их в обратной последовательности (audit_access первым =
    # innermost, rate_limit последним из `@app.middleware`, плюс
    # `SecurityHeadersMiddleware` добавляется через `add_middleware` ниже и
    # становится истинным outermost'ом — заголовки попадают на КАЖДЫЙ ответ
    # (включая 429 rate-limit и 422 валидации), а не только на route-ответы).
    #
    # Зачем такой порядок:
    #   * `SecurityHeadersMiddleware` outermost (см. `add_middleware` ниже) —
    #     HSTS/X-Frame-Options/X-Content-Type-Options/etc. навешиваются на
    #     любой ответ, в том числе ранние 429 и 422.
    #   * `rate_limit_middleware` outermost среди `@app.middleware`-стэка —
    #     429-ответ всплывает мимо `audit_access`, никакого `http.client_error`
    #     audit-emit'а на rate-limited трафике (иначе slowloris дудосит сам
    #     audit-канал).
    #   * `attach_request_id_and_context` — выставляет request_id и
    #     audit_context для всего, что прошло rate-limit. Platform-admin
    #     guard ниже использует этот request_id в 403-envelope.
    #   * `platform_admin_guard` — отбивает 403 для
    #     account_admin/loging_admin/loging_reader ДО `audit_access`. Это
    #     значит, что platform-admin attempt НЕ эмитит дополнительный
    #     `http.access_denied` (audit_access ниже не запускается, так как
    #     middleware возвращает response без `call_next`), а только наш
    #     более специфичный `http.platform_admin_blocked` (см. §7-8
    #     security-модели в `1.txt` репо).
    #   * `audit_access` innermost — видит финальный response от route и
    #     эмитит audit на 4xx/5xx внутри контекста, выставленного выше.

    @app.middleware("http")
    async def audit_access(request: Request, call_next):
        """Inner-most middleware: на 4xx/5xx ответе пишет http.* audit-event.

        401/403 → `http.access_denied` (CRITICAL).
        Прочие 4xx (кроме 501) → `http.client_error` (WARNING).
        5xx → `http.server_error` (CRITICAL).
        501 и health-paths пропускаются ранним return'ом.
        """
        response = await call_next(request)
        path = request.url.path
        if path in _HEALTH_PATHS:
            return response
        status_code = response.status_code
        if status_code < 400:
            return response

        # 501 NOT_IMPLEMENTED — заглушка эндпоинта, не действие юзера.
        # 41 stub-эндпоинт отдаёт `AppException(501, NOT_IMPLEMENTED)` — для
        # audit'а это шум. Authenticated insider циклом по stub'ам эмитил бы
        # 41 `http.server_error` за круг (audit-amplification). Реальные 5xx
        # (баги, БД-сбои, worker_unreachable) по-прежнему пишутся — фактический
        # 500 сейчас возможен только через runtime-ошибку, не explicit raise.
        if status_code == 501:
            return response

        details = {"method": request.method, "path": path, "status_code": status_code}

        if status_code in (401, 403):
            action, emit_status = "http.access_denied", "denied"
        elif status_code < 500:
            action, emit_status = "http.client_error", "failure"
        else:
            action, emit_status = "http.server_error", "failure"

        # context уже выставлен внешним middleware — emit() сам подхватит ua/ip.
        # ВНИМАНИЕ: НЕ оборачиваем emit в `asyncio.to_thread` — `emit` сам
        # детектит running loop и шедулит запрос через pooled `_audit_client`
        # (см. `audit_service.py:144 loop.create_task(...)`).
        # С `to_thread` worker-thread не имеет running loop → emit'у попадает
        # в sync-fallback `httpx.post` мимо pool'а из lifespan. Прямой вызов
        # из async-контекста — корректный путь.
        audit_service.emit(
            action,
            status=emit_status,
            allowed=False,
            details=details,
        )
        return response

    # Platform-admin guard — регистрируется ПОСЛЕ `audit_access` (значит
    # располагается БЛИЖЕ к route, чем attach_request_id ниже), но
    # технически в final-стеке окажется между attach_request_id (outer) и
    # audit_access (inner). См. длинный комментарий выше про reverse-стек
    # Starlette. Логика middleware и обоснование — в
    # `src/middleware/platform_admin_guard.py`.
    app.middleware("http")(platform_admin_guard)

    @app.middleware("http")
    async def attach_request_id_and_context(request: Request, call_next):
        """Middle middleware: ставит request_id и audit_context на каждый запрос."""
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id

        ip = audit_context.extract_client_ip(request)
        ua = request.headers.get("User-Agent")
        # actor_id / username / department_id заполнятся позже после introspect
        # в endpoint'е — здесь у нас ещё нет identity. Но ip/ua/request_id уже есть.
        ctx = AuditContext(
            request_id=request_id,
            ip_address=ip,
            user_agent=ua,
        )
        token = audit_context.set_context(ctx)
        try:
            response = await call_next(request)
        finally:
            audit_context.reset_context(token)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """Per-IP rate-limit с bypass'ом для health/ready (slowloris часть b).

        Этот middleware регистрируется ПОСЛЕДНИМ → становится outermost
        в стеке. Это значит: 429-ответ возвращается ДО того, как запрос
        дойдёт до `audit_access`, и rate-limited трафик НЕ порождает
        `http.client_error` audit-event'ов (фикс audit-amplification).

        Также 429-ответ ещё не имеет `request.state.request_id` —
        `_rate_limit_exceeded_response` отдаёт `None` в этом поле, что
        допустимо для отбитого трафика (не нужен лог-trace).
        """
        if not _is_health_path(request.url.path):
            try:
                limiter._check_request_limit(request, None, True)
            except RateLimitExceeded as exc:
                return _rate_limit_exceeded_response(request, exc)
        return await call_next(request)

    # HTTPSRequiredMiddleware регистрируем перед SecurityHeadersMiddleware,
    # чтобы security-headers ОБОРАЧИВАЛИ его 403-ответ: HSTS на отбитом
    # cleartext-запросе бесполезен, но X-Frame-Options/X-Content-Type-Options
    # и CSP должны висеть на любом ответе сервиса, включая HTTPS_REQUIRED.
    # В dev/test/local middleware просто пропускает (self._enabled=False).
    # В production/staging cleartext-HTTP отбивается 403 ДО rate-limit'а,
    # introspect-call'а и audit-канала — никакого http-flood'а в audit.
    # Health/ready пропускаются всегда — k8s probe ходит на pod-network http.
    app.add_middleware(
        HTTPSRequiredMiddleware,
        app_env=settings.app_env,
    )

    # SecurityHeadersMiddleware регистрируем последним → outermost слой.
    # Так заголовки попадают на КАЖДЫЙ ответ, включая 429 от rate-limit и
    # 422 от валидации, а не только на route-ответы.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.security_hsts_enabled,
    )

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        """Превращает любое AppException в стандартный envelope с request_id/timestamp."""
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

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Pydantic validation → 422 с тем же envelope."""
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

    def custom_openapi():
        """Кастомный OpenAPI-генератор — добавляет BearerAuth security-scheme."""
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
                "description": "Access token issued by auth_service (POST /api/auth/v1/login)",
            },
        }
        schema["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


async def _drain_pending_audit_tasks() -> None:
    """Дождаться завершения in-flight audit emit-task'ов.

    `audit_service.emit()` шедулит fire-and-forget через
    `loop.create_task(_send_to_logging_service(...))` и трекает task'и в
    `audit_service._pending_audit_tasks`. Они держат pooled `_audit_client`;
    закрытие pool'а до их завершения — task'а падает в `ClientClosedError`,
    audit-event теряется. Например, последний `http.client_error` для
    in-flight 4xx response, когда уже пришёл SIGTERM.

    Алгоритм:

    1. Снимок текущего набора pending audit-task'ов (`set()` копия —
       избегаем mutation during iteration через done-callback).
    2. Исключаем уже завершённые + текущий task (последнее не должно
       случиться, но belt-and-braces).
    3. `asyncio.wait` с таймаутом `_AUDIT_DRAIN_TIMEOUT_SECONDS`. Что не
       успело — потеряется (нам нужно вернуть управление lifespan'у, k8s
       ждёт SIGTERM завершения в `terminationGracePeriodSeconds`).

    Best-effort: любые ошибки внутри глушим в WARNING, lifespan не должен
    падать на shutdown.
    """
    try:
        current = asyncio.current_task()
        pending = {
            t for t in audit_service._pending_audit_tasks
            if not t.done() and t is not current
        }
        if not pending:
            return

        await asyncio.wait(pending, timeout=_AUDIT_DRAIN_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — best-effort на shutdown
        logger.warning("audit drain failed (%s: %s)", type(exc).__name__, exc)


async def _run_startup_audit_sequence() -> None:
    """Регистрация event-каталога в loging_service + эмит `service.started` lifecycle-event.

    Вызывается из `lifespan` под `asyncio.wait_for(timeout=_STARTUP_AUDIT_TIMEOUT_SECONDS)`.

    Отличия от прежнего sync-варианта:

    * `register_events()` — sync `httpx.post(timeout=5.0)` — выполняется через
      `asyncio.to_thread`, чтобы не блокировать event-loop, но дождаться
      завершения (или таймаута wait_for'а сверху).
    * `service.started` эмитится с **system-actor**: `actor_type="system"`,
      `actor_id="server_service"`, `username=None`, `department_id=None`,
      `request_id="startup-<pid>"`. Это lifecycle-event сервиса, не действие
      пользователя — в audit-логе он не должен иметь fake-user identity.
    * `service.started` уходит через async-path (`emit` запланирует
      `loop.create_task(_send_to_logging_service)` поверх pooled
      `_audit_client`); следующий `await asyncio.sleep(0)` отдаёт управление
      loop'у, чтобы task'а успела отправиться до того, как мы вернёмся из
      lifespan startup-half'а.

    Сетевые сбои в `register_events()` / `emit()` глушатся внутри (best-effort,
    `httpx.HTTPError` → WARNING-лог). Внешний caller дополнительно ловит
    `asyncio.TimeoutError` / любые другие исключения и логгирует WARNING.
    """
    # Каталог событий — sync httpx.post timeout=5.0 внутри. В отдельном thread'е,
    # чтобы не блокировать event-loop на slow loging_service. wait_for сверху
    # отрубит весь блок если to_thread сам зависнет.
    await asyncio.to_thread(register_events)

    # service.started — lifecycle, не пользовательский action. Явно
    # перетираем actor/username/department из (потенциально пустого)
    # contextvar'а: на startup у нас нет http-request, и фоновые middleware
    # пути не должны просочиться сюда.
    audit_service.emit(
        "service.started",
        actor_id="server_service",
        actor_type="system",
        username=None,
        department_id=None,
        request_id=f"startup-{os.getpid()}",
    )

    # `emit` шедулит fire-and-forget task через `loop.create_task` — отдаём
    # event-loop'у управление, чтобы task попала в очередь и начала слать
    # запрос ДО возврата из lifespan startup. Иначе lifespan yield'нул бы
    # раньше первого `await client.post(...)`, и task отменилась бы на
    # shutdown.
    await asyncio.sleep(0)


def _http_status_to_category(status: int) -> str:
    """HTTP code → строковая категория для envelope.error."""
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
