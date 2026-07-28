"""Entrypoint приложения server_service. FastAPI app + middleware + lifespan."""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.router import api_router
from src.core import http_clients
from src.core.config import get_settings
from src.core.constants import HEALTH_PATHS
from src.core.limiter import limiter
from src.core.exceptions import AppException
from src.core.logging import configure_logging, request_id_var
from src.dependencies import auth as auth_deps
from src.middleware.https_guard import HTTPSRequiredMiddleware
from src.middleware.platform_admin_guard import platform_admin_guard
from src.middleware.reencrypt_gate import reencrypt_maintenance_gate
from src.services import (
    audit_context,
    audit_service,
    http_pool,
    secrets_drainer,
    worker_client,
)
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

def _is_health_path(path: str) -> bool:
    """True если path относится к health/ready (исключён из rate-limit'а).

    Источник — `core/constants.HEALTH_PATHS` (тот же набор, что и в
    `middleware/platform_admin_guard`).
    """
    return path in HEALTH_PATHS


# Пути, на которых CSP ослабляется под Swagger UI: бандл грузится скриптом и
# стилем, поэтому строгий `default-src 'none'` отдаёт белую страницу. Послабление
# точечное — на остальных путях API остаётся закрытым.
_SWAGGER_CSP_PATHS = frozenset({"/docs", "/docs/oauth2-redirect"})

# Источник ассетов по умолчанию, если self-host база не задана.
_SWAGGER_CDN = "https://cdn.jsdelivr.net"


# SOURCE OF TRUTH: dbos_server_service/sdk/security_headers.py
# DUPE: keep in sync with auth_service/loging_service/server_service security_headers.py
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security headers (HSTS опционально, X-Frame-Options, CSP).

    HSTS включается только при `SECURITY_HSTS_ENABLED=true` — за http-фронтом
    он сломает rebound. Зеркалит auth_service / loging_service.
    """

    def __init__(self, app, *, hsts_enabled: bool, assets_base: str = ""):
        super().__init__(app)
        self._hsts_enabled = hsts_enabled
        self._assets_base = assets_base.rstrip("/")

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        # CSP для JSON-API минимальный — disallow всё лишнее. На путях Swagger
        # UI ослабляем ровно настолько, чтобы загрузился бандл (скрипт, стиль,
        # шрифты/иконки), оставляя `frame-ancestors 'none'`.
        if request.url.path in _SWAGGER_CSP_PATHS:
            base = self._assets_base or _SWAGGER_CDN
            response.headers.setdefault(
                "Content-Security-Policy",
                (
                    "default-src 'none'; "
                    f"script-src 'self' 'unsafe-inline' {base}; "
                    f"style-src 'self' 'unsafe-inline' {base}; "
                    f"img-src 'self' data: {base}; "
                    "connect-src 'self'; "
                    "frame-ancestors 'none'"
                ),
            )
        else:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'",
            )
        # Permissions-Policy: JSON-API без UI, зануляем sensor-API на случай,
        # если когда-нибудь появится браузерный клиент.
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


def _rate_limit_exceeded_response(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Кастомный 429-ответ, совместимый по shape с `AppException`.

    Поверх envelope'а проставляем standard rate-limit заголовки: `Retry-After`
    (фиксированные 60 сек — клиенту понятная пауза до повтора),
    `X-RateLimit-Limit` (текстовое описание пробитого правила, как его видит
    slowapi), плюс `X-RateLimit-Remaining: 0` / `X-RateLimit-Reset` для
    совместимости с типовыми клиентами, ожидающими IETF-draft набор.
    """
    retry_after = 60
    limit_repr = str(exc.detail)
    reset_epoch = int(datetime.now(timezone.utc).timestamp()) + retry_after
    return JSONResponse(
        status_code=429,
        content={
            "error": "too_many_requests",
            "error_code": "RATE_LIMIT_EXCEEDED",
            "message": f"Rate limit exceeded: {exc.detail}",
            "details": {"limit": limit_repr},
            "request_id": getattr(request.state, "request_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": limit_repr,
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset_epoch),
        },
    )


def _register_self_hosted_docs(app: FastAPI, assets_base: str) -> None:
    """Отдать Swagger UI с локального бандла вместо CDN jsdelivr.

    Корп-сеть не видит cdn.jsdelivr.net, поэтому дефолтный /docs приходит
    белой страницей. Когда задан SWAGGER_UI_ASSETS_BASE, штатный docs_url
    отключается, а здесь регистрируется свой /docs, тянущий bundle/css с
    этого адреса. openapi_url остаётся same-origin.
    """
    base = assets_base.rstrip("/")

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui_html():
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} — Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_js_url=f"{base}/swagger-ui-bundle.js",
            swagger_css_url=f"{base}/swagger-ui.css",
            swagger_ui_parameters=app.swagger_ui_parameters,
        )

    @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
    async def swagger_ui_redirect():
        return get_swagger_ui_oauth2_redirect_html()


def create_application() -> FastAPI:
    """Собрать FastAPI app: lifespan + middleware + exception handlers + роутер."""
    settings = get_settings()
    # `APP_LOG_LEVEL` — не оформлен в Settings (server_service исторически
    # не имел log-level настройки в config'е), читаем напрямую из env.
    configure_logging("server_service", level=os.environ.get("APP_LOG_LEVEL", "INFO"))

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
        # Поднимаем pooled httpx.AsyncClient'ы под исходящие каналы:
        # introspect → auth_service, audit-emit → loging_service, read → loging_service.
        # Конструирование клиентов и лимиты вынесены в `services/http_pool.init_pools` —
        # здесь lifespan только дёргает init/aclose, чтобы вся конфигурация
        # пулов жила в одном модуле (зеркалит `server_worker`).
        http_pool.init_pools(settings)
        # Pooled aioredis-клиент для bootstrap-кред prepare'а. До этого
        # `store_prepare_creds` строил `aioredis.from_url(...)` per-call —
        # burst POST /prepare ронял Redis на connection-budget. Если
        # `server_worker_redis_url` не задан, пул не создаём — сам
        # `store_prepare_creds` поднимет WORKER_REDIS_NOT_CONFIGURED.
        if settings.server_worker_redis_url:
            worker_client._creds_redis_client = aioredis.from_url(
                settings.server_worker_redis_url,
            )
        # Загружаем настраиваемую парольную политику из БД в процессный кэш
        # `core/password_policy`. Валидаторы паролей синхронные и читают кэш;
        # без этого шага они работали бы на дефолтах до первого PUT. Если
        # таблицы/строки ещё нет (свежая БД до миграции) — не падаем, оставляем
        # дефолты кэша.
        await _load_password_policy()
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
        # Фоновый self-drain reencrypt-outbox. Осушает миграцию секретов без
        # server_worker'а (taskiq-scheduler'а в проде нет). Реплики шардятся
        # через `FOR UPDATE SKIP LOCKED` в `claim_pending`. Отключается
        # `REENCRYPT_DRAIN_ENABLED=false`.
        drain_loop: secrets_drainer.DrainLoop | None = None
        if settings.reencrypt_drain_enabled:
            drain_loop = secrets_drainer.DrainLoop(settings)
            drain_loop.start()
            app.state.reencrypt_drain_loop = drain_loop

        try:
            yield
        finally:
            # Дренер останавливаем ПЕРВЫМ: он ходит в БД через AsyncSessionLocal,
            # надо погасить его до dispose'а главного engine ниже.
            if drain_loop is not None:
                await drain_loop.stop()

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

            # Read-канал к loging закрываем после audit-pool'а — оба
            # держат соединения до того же хоста, но в раздельных пулах,
            # порядок между ними не критичен.
            loging_read = http_clients.loging_read_client
            http_clients.loging_read_client = None
            if loging_read is not None:
                await loging_read.aclose()

            # Pooled prepare-creds Redis-клиент закрываем последним: с этого
            # момента входящих POST /prepare уже нет (uvicorn graceful drain
            # отработал выше), а `_creds_redis_client = None` сбрасывает
            # модульный slot, чтобы повторный запуск lifespan (в тестах через
            # `app.router.lifespan_context`) не наследовал закрытый client.
            prepare_redis = worker_client._creds_redis_client
            worker_client._creds_redis_client = None
            if prepare_redis is not None:
                await prepare_redis.aclose()

            # taskiq-broker держит свой Redis-pool — закрываем его и
            # сбрасываем модульные `_worker_broker`/`_broker_started`, чтобы
            # повторный lifespan (в тестах) поднял свежий broker, а не
            # реиспользовал закрытый.
            await worker_client.shutdown_broker()

            # Главный DB engine: закрываем самым последним, после всех
            # HTTP/Redis pool'ов и worker-broker'а. К этой точке in-flight
            # endpoint-запросы уже завершили транзакции (uvicorn graceful
            # drain + closed introspect/audit pools), и держать live PG-pool
            # больше незачем. Без dispose'а тесты через `lifespan_context`
            # копят коннекты между прогонами, в k8s — pod уходит в TIME_WAIT
            # на pgbouncer'е дольше, чем нужно.
            from src.db.session import engine as _main_engine
            await _main_engine.dispose()

    # В production закрываем публичный OpenAPI/Swagger UI — анонимы не должны
    # видеть каталог эндпоинтов (включая stub-501 с summary вроде «Reveal decrypted
    # IPMI credentials»). В dev/test/local остаётся открытым для разработки.
    is_production = settings.app_env.lower() == "production"
    # Self-host Swagger UI: когда задан SWAGGER_UI_ASSETS_BASE, дефолтный
    # docs_url выключаем и ставим свой /docs (см. ниже), иначе оставляем
    # штатный вариант с CDN.
    self_host_docs = (not is_production) and bool(settings.swagger_ui_assets_base)
    openapi_url = None if is_production else "/openapi.json"
    docs_url = None if (is_production or self_host_docs) else "/docs"
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

    if self_host_docs:
        _register_self_hosted_docs(app, settings.swagger_ui_assets_base)

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
        if path in HEALTH_PATHS:
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

    # Maintenance-gate форсированной перешифровки. Регистрируется ПОСЛЕ
    # platform_admin_guard (значит в стеке — снаружи него и audit_access), но
    # ВНУТРЬ attach_request_id_and_context ниже: 503 REENCRYPT_IN_PROGRESS
    # короткозамкнут до audit_access (не флудим `http.server_error`), но уже
    # имеет request_id в envelope. Логика и exempt-пути — в
    # `middleware/reencrypt_gate.py`.
    app.middleware("http")(reencrypt_maintenance_gate)

    @app.middleware("http")
    async def attach_request_id_and_context(request: Request, call_next):
        """Middle middleware: ставит request_id и audit_context на каждый запрос."""
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        # ContextVar для JSON-логгера — попадает в `request_id` поле каждой
        # log-записи внутри этого запроса.
        rid_token = request_id_var.set(request_id)

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
            request_id_var.reset(rid_token)
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
        trusted_proxy_ips=list(settings.trusted_proxy_ips or []),
    )

    # SecurityHeadersMiddleware регистрируем последним → outermost слой.
    # Так заголовки попадают на КАЖДЫЙ ответ, включая 429 от rate-limit и
    # 422 от валидации, а не только на route-ответы.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.security_hsts_enabled,
        assets_base=settings.swagger_ui_assets_base,
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

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        """IntegrityError fallthrough → 422 на CHECK violation, иначе 500.

        Сервисный слой ловит UNIQUE-нарушения сам и поднимает осмысленный
        ConflictError (`SERVER_DUPLICATE`, `IPMI_DUPLICATE`, ...). Сюда
        долетают только не пойманные IntegrityError — почти всегда это
        CHECK / FK violation, которые сервисный слой не разобрал
        (например, новые CHECK constraints на `ssh_port`, `unix_groups`).
        В таких случаях клиентский ввод нарушил contract, ответ — 422
        DOMAIN_CONSTRAINT_VIOLATION, а не 500 INTERNAL_ERROR.

        UNIQUE violation сюда попасть может только если сервис забыл
        обернуть INSERT в try/except — тогда отдадим 409 (по PostgreSQL
        sqlstate 23505), чтобы клиент не ловил 500 на duplicate.
        """
        orig = getattr(exc, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        # 23505 = unique_violation; 23514 = check_violation; 23503 = foreign_key_violation.
        if sqlstate == "23505":
            http_status = 409
            error_code = "INTEGRITY_VIOLATION_UNIQUE"
            message = "Unique constraint violation"
        elif sqlstate in {"23514", "23503", "23502"}:
            http_status = 422
            error_code = "DOMAIN_CONSTRAINT_VIOLATION"
            message = "Database constraint violation (check/foreign-key/not-null)"
        else:
            http_status = 500
            error_code = "INTERNAL_ERROR"
            message = "Database integrity error"
        logger.warning(
            "Unclassified IntegrityError на %s: sqlstate=%s class=%s",
            getattr(request, "url", "?"), sqlstate, type(orig).__name__,
        )
        return JSONResponse(
            status_code=http_status,
            content={
                "error": _http_status_to_category(http_status),
                "error_code": error_code,
                "message": message,
                "details": {"sqlstate": sqlstate} if sqlstate else {},
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

    Sync-path `_send_sync` (`audit_service.py:267`) **не дренируется** этой
    функцией — он работает синхронным `httpx.post` без `_pending_audit_tasks`
    регистрации. На SIGTERM узкое окно: если в момент сигнала висит sync-emit
    с retry'ями, lifespan ждёт его блокирующе через GIL — это не баг, а
    осознанное упрощение (sync-path используется только в startup/shutdown
    хуках, где async loop ещё/уже недоступен).
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


async def _load_password_policy() -> None:
    """Прочитать singleton парольной политики из БД в процессный кэш.

    Best-effort: на свежей БД до миграции таблицы ещё нет — ловим любую ошибку,
    логгируем WARNING и едем на дефолтах кэша (историческое поведение). Открываем
    отдельную короткую сессию, чтобы не завязываться на request-scoped `get_db`.
    """
    from src.core import password_policy
    from src.core.config import get_settings
    from src.db.session import AsyncSessionLocal
    from src.services import password_policy_service

    # Начальная политика из env — база «из коробки». Строка из БД (её пишет
    # account_admin через UI) при наличии перекроет это в load_active_policy.
    settings = get_settings()
    password_policy.apply_policy(
        {
            "min_length": settings.password_policy_min_length,
            "require_letter": settings.password_policy_require_letter,
            "require_digit": settings.password_policy_require_digit,
        }
    )
    try:
        async with AsyncSessionLocal() as session:
            await password_policy_service.load_active_policy(session)
    except Exception as exc:  # noqa: BLE001 — best-effort: не блокируем startup
        logger.warning(
            "password policy load skipped (%s: %s) — using default policy",
            type(exc).__name__,
            exc,
        )


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
        410: "gone",
        422: "validation_error",
        429: "too_many_requests",
        503: "service_unavailable",
    }.get(status, "internal_error")


app = create_application()
