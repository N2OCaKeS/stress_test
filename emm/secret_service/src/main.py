"""Entrypoint приложения secret_service. FastAPI app + middleware + lifespan."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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

from src.api.router import api_router
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.limiter import limiter
from src.core.logging import configure_logging, request_id_var
from src.core.security import SecurityHeadersMiddleware
from src.middleware.audit_middleware import AuditAccessMiddleware
from src.middleware.https_guard import HTTPSRequiredMiddleware
from src.middleware.maintenance_gate import ReencryptMaintenanceMiddleware
from src.services import audit_context, audit_events

logger = logging.getLogger("secret_service.startup")


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
    configure_logging("secret_service", level=settings.app_log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Регистрация audit-событий в loging_service (best-effort).
        try:
            audit_events.register_events()
        except Exception as exc:  # noqa: BLE001 — startup-best-effort
            logger.warning("audit events registration failed: %s", exc)

        # Сверка keystore с durable-пометкой выведенных версий: если файл
        # keystore исчез (emptyDir при рестарте пода) и bootstrap воскресил
        # выведенную версию из env — вычищаем её снова. Best-effort, не в test.
        if settings.app_env.lower() != "test":
            try:
                from src.db.session import AsyncSessionLocal
                from src.services import key_rotation_service

                async with AsyncSessionLocal() as reconcile_db:
                    pruned = await key_rotation_service.reconcile_tombstones(reconcile_db)
                if pruned:
                    logger.warning("keystore tombstone reconcile pruned %s", pruned)
            except Exception as exc:  # noqa: BLE001 — startup-best-effort
                logger.warning("keystore tombstone reconcile failed: %s", exc)

        # Фоновый sweep — только если включён и не в test-env. Test-suite
        # дёргает sweep_expired_blocked напрямую без loop'а.
        import asyncio as _asyncio
        sweep_task: _asyncio.Task | None = None
        if settings.sweep_enabled and settings.app_env.lower() != "test":
            from src.services.sweep_service import sweep_loop as _sweep_loop
            sweep_task = _asyncio.create_task(_sweep_loop())

        # Self-drain loop: перешифровка reencrypt-outbox прямо в процессе
        # (в проде нет отдельного worker'а). В test-env выключен — тесты
        # дёргают drain_tick напрямую.
        drain_task: _asyncio.Task | None = None
        if settings.reencrypt_drain_enabled and settings.app_env.lower() != "test":
            from src.services.reencrypt_drain_service import drain_loop as _drain_loop
            drain_task = _asyncio.create_task(_drain_loop())

        try:
            yield
        finally:
            for _bg_task, _name in ((sweep_task, "sweep"), (drain_task, "drain")):
                if _bg_task is None:
                    continue
                _bg_task.cancel()
                try:
                    await _bg_task
                except (_asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.debug("%s task shutdown: %s", _name, exc)
            from src.db.session import engine as _main_engine
            await _main_engine.dispose()

    is_production = settings.app_env.lower() == "production"
    # Self-host Swagger UI: когда задан SWAGGER_UI_ASSETS_BASE, дефолтный
    # docs_url выключаем и ставим свой /docs (см. ниже), иначе оставляем
    # штатный вариант с CDN.
    self_host_docs = (not is_production) and bool(settings.swagger_ui_assets_base)
    openapi_url = None if is_production else "/openapi.json"
    docs_url = None if (is_production or self_host_docs) else "/docs"
    redoc_url = None if is_production else "/redoc"

    app = FastAPI(
        title="secret_service",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        swagger_ui_parameters={"persistAuthorization": True},
        lifespan=lifespan,
    )

    if self_host_docs:
        _register_self_hosted_docs(app, settings.swagger_ui_assets_base)

    # Регистрация limiter'а в app.state (slowapi-декораторы ищут именно там).
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_response)

    # Starlette стакает `@app.middleware("http")` в обратном порядке регистрации,
    # последний зарегистрированный = outermost. Чтобы получить порядок
    # outer → inner: SecurityHeaders → HTTPSGuard → attach_request_id → audit_access → route,
    # регистрируем audit_access первым (innermost), attach_request_id затем,
    # а HTTPSGuard и SecurityHeaders через add_middleware (они становятся
    # outermost'ами и оборачивают всё, включая ранние 422/429).

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        """request_id + audit_context на каждый запрос."""
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        # ContextVar для JSON-логгера — попадает в `request_id` поле каждой
        # log-записи внутри этого запроса.
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

    # add_middleware-стек идёт в обратном порядке: последний добавленный = outermost.
    # Желаемый порядок (outer→inner): SecurityHeaders → HTTPSGuard → SlowAPI → attach_request_id → audit_access → route.
    # audit_access добавляем ПЕРВЫМ через add_middleware (innermost), потом
    # attach_request_id уже зарегистрирован через @app.middleware выше.
    # SlowAPIMiddleware регистрируется ПОСЛЕ audit_access — выше по стеку,
    # чтобы 429-ответ от endpoint-декораторов `@limiter.limit(...)` всплывал
    # МИМО audit_access и НЕ порождал `http.client_error` audit-event'ов на
    # rate-limited трафике (защита audit-канала от amplification).
    # HTTPSGuard и SecurityHeaders регистрируем последними → outermost.
    app.add_middleware(AuditAccessMiddleware)
    app.add_middleware(SlowAPIMiddleware)

    # Maintenance-gate регистрируется ПОСЛЕ audit/slowapi → выше по стеку:
    # 503 REENCRYPT_IN_PROGRESS в force-окне всплывает мимо audit'а, без
    # amplification'а на заблокированном трафике. status/health/ready gate
    # пропускает (см. middleware).
    app.add_middleware(ReencryptMaintenanceMiddleware)

    # HTTPSRequiredMiddleware регистрируется ПЕРЕД SecurityHeadersMiddleware,
    # чтобы security-headers оборачивали 403 cleartext-ответ. В dev/test/local
    # middleware просто пропускает (self._enabled=False).
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
