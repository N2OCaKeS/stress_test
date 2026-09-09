"""Entrypoint приложения testing_service. FastAPI app + middleware + lifespan."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
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
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.exc import IntegrityError

from src.api.internal_router import internal_router
from src.api.router import api_router
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.limiter import limiter
from src.core.logging import configure_logging, request_id_var
from src.core.security import SecurityHeadersMiddleware
from src.dependencies import auth as auth_deps
from src.middleware.audit_middleware import AuditAccessMiddleware
from src.middleware.https_guard import HTTPSRequiredMiddleware
from src.services import audit_context, audit_events, audit_service, log_rotation

logger = logging.getLogger("testing_service.startup")

# Сколько ждём in-flight audit-emit task'и при shutdown'е, прежде чем закрыть
# pooled audit-client под ними. Symmetричный бюджет с остальными сервисами.
_AUDIT_DRAIN_TIMEOUT_SECONDS = 2.0


def _rate_limit_exceeded_response(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Кастомный 429 — наш стандартный envelope + Retry-After."""
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
    этого адреса.
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


async def _log_rotation_loop(interval_seconds: float) -> None:
    """Фоновая ротация логов (§8.5 плана миграции) — раз в сутки по умолчанию.

    `testing_service` — чистый FastAPI без своего брокера/scheduler'а (в
    отличие от `testing_worker`), поэтому обычного `asyncio.create_task` в
    lifespan достаточно вместо полноценного taskiq-периодика: план явно не
    требует ежеминутной точности для этой политики. Пересчёт `protected` по
    всем веткам — safety-net поверх пересчёта "на лету" при создании лога
    (`services/test_log.py::get_or_create_log`); затем удаление устаревших
    незащищённых логов (`settings.log_retention_days`).
    """
    from src.db.session import AsyncSessionLocal

    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            settings = get_settings()
            async with AsyncSessionLocal() as db:
                await log_rotation.recompute_all_branches(db)
                deleted = await log_rotation.enforce_monthly_retention(db, settings.log_retention_days)
                if deleted:
                    logger.info("log rotation: deleted %d stale unprotected log(s)", deleted)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 — периодическая job не должна ронять процесс
            logger.warning("log rotation loop failed: %s", exc)


async def _drain_pending_audit_tasks() -> None:
    """Дать шанс дойти до сети in-flight audit-emit task'ам перед закрытием пула."""
    pending = [t for t in audit_service._pending_audit_tasks if not t.done()]
    if not pending:
        return
    _done, still_pending = await asyncio.wait(pending, timeout=_AUDIT_DRAIN_TIMEOUT_SECONDS)
    if still_pending:
        logger.warning(
            "shutdown: %d audit-emit task(s) did not finish within %.1fs",
            len(still_pending), _AUDIT_DRAIN_TIMEOUT_SECONDS,
        )


def create_application() -> FastAPI:
    """Собрать FastAPI app: lifespan + middleware + exception handlers + роутер."""
    settings = get_settings()
    configure_logging("testing_service", level=settings.app_log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Pooled outbound-клиенты: introspect (auth_deps) и audit-emit
        # (audit_service). Один keep-alive пул на процесс вместо свежего
        # TCP+TLS handshake на каждый вызов.
        auth_deps._introspect_client = httpx.AsyncClient(
            base_url=settings.auth_service_url,
            timeout=settings.auth_request_timeout_seconds,
            verify=settings.introspect_tls_verify,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        if settings.logging_service_url:
            audit_service._audit_client = httpx.AsyncClient(
                base_url=settings.logging_service_url,
                timeout=2.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )

        # Регистрация audit-событий в loging_service (best-effort).
        try:
            audit_events.register_events()
        except Exception as exc:  # noqa: BLE001 — startup-best-effort
            logger.warning("audit events registration failed: %s", exc)

        rotation_task = asyncio.create_task(
            _log_rotation_loop(settings.log_rotation_interval_seconds)
        )

        try:
            yield
        finally:
            rotation_task.cancel()
            try:
                await rotation_task
            except asyncio.CancelledError:
                pass

            # Shutdown order: introspect-client → drain pending audit-tasks →
            # audit-client. Закрытие introspect'а первым безопасно — uvicorn
            # graceful shutdown уже перестал принимать новые запросы к этому
            # моменту. audit-client закрываем последним, чтобы fire-and-forget
            # emit-таски успели уйти в сеть под ещё живым пулом.
            introspect = auth_deps._introspect_client
            auth_deps._introspect_client = None
            if introspect is not None:
                await introspect.aclose()

            await _drain_pending_audit_tasks()

            audit_pool = audit_service._audit_client
            audit_service._audit_client = None
            if audit_pool is not None:
                await audit_pool.aclose()

            from src.db.session import engine as _main_engine
            await _main_engine.dispose()

    is_production = settings.app_env.lower() == "production"
    self_host_docs = (not is_production) and bool(settings.swagger_ui_assets_base)
    openapi_url = None if is_production else "/openapi.json"
    docs_url = None if (is_production or self_host_docs) else "/docs"
    redoc_url = None if is_production else "/redoc"

    app = FastAPI(
        title="testing_service",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        swagger_ui_parameters={"persistAuthorization": True},
        lifespan=lifespan,
    )

    if self_host_docs:
        _register_self_hosted_docs(app, settings.swagger_ui_assets_base)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_response)

    # Starlette стакает `@app.middleware("http")` в обратном порядке регистрации,
    # последний зарегистрированный = outermost. Желаемый порядок outer→inner:
    # SecurityHeaders → HTTPSGuard → SlowAPI → attach_request_id → audit_access → route.

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        """request_id + audit_context на каждый запрос."""
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        rid_token = request_id_var.set(request_id)

        ip = request.client.host if request.client else None
        ua = request.headers.get("User-Agent")
        ctx = audit_context.AuditContext(
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

    # add_middleware-стек — последний добавленный = outermost.
    app.add_middleware(AuditAccessMiddleware)
    app.add_middleware(SlowAPIMiddleware)

    # HTTPSRequiredMiddleware — перед SecurityHeadersMiddleware, чтобы
    # security-headers оборачивали 403 cleartext-ответ. В dev/test/local
    # middleware просто пропускает.
    app.add_middleware(
        HTTPSRequiredMiddleware,
        app_env=settings.app_env,
    )

    # SecurityHeadersMiddleware — outermost.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.security_hsts_enabled,
        assets_base=settings.swagger_ui_assets_base,
    )

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        """Доменное исключение → стандартный envelope."""
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
        """Pydantic validation → 422 с error-envelope."""
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
        """IntegrityError fallthrough → 409 на UNIQUE, 422 на CHECK/FK, иначе 500."""
        orig = getattr(exc, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
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
    # Внутренние service-to-service роутеры (callback prepare-for-test,
    # claim/completed для testing_worker) — вне /api/testing/v1 namespace,
    # см. `src/api/internal_router.py`.
    app.include_router(internal_router)

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
