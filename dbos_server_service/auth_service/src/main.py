"""Application entrypoint for auth_service."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from src.api.router import api_router
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.logging import configure_logging
from src.dependencies.db import get_db
from src.services import audit_context, audit_service
from src.services.audit_context import AuditContext
from src.services.audit_events import register_events
from src.services.bootstrap_service import bootstrap_admin

_HEALTH_PATHS = {"/api/auth/v1/health", "/api/auth/v1/ready"}


def create_application() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_log_level)

    _is_prod = settings.app_env == "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async for db in get_db():
            await bootstrap_admin(db)
        asyncio.ensure_future(asyncio.to_thread(_startup_sequence))
        yield

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

    @app.middleware("http")
    async def attach_request_id_and_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id

        # Заполняем audit_context из JWT (если есть) и сетевого контекста.
        actor_id, username, department_id = _extract_actor_info(request)
        ip = _extract_client_ip(request)
        ua = request.headers.get("User-Agent")
        ctx = AuditContext(
            actor_id=actor_id,
            username=username,
            department_id=department_id,
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
    async def audit_access(request: Request, call_next):
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

        # context уже выставлен внешним middleware — emit() сам подхватит actor/ua/ip
        asyncio.ensure_future(
            asyncio.to_thread(
                audit_service.emit,
                action,
                status=emit_status,
                allowed=False,
                details=details,
            )
        )
        return response

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

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "error_code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": exc.errors()},
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    app.include_router(api_router, prefix="/api")

    def custom_openapi():
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
                "description": "Paste your access_token from POST /api/auth/v1/login",
            },
            "OAuth2Password": {
                "type": "oauth2",
                "description": "Login with username + password (Swagger form)",
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


def _startup_sequence() -> None:
    """Run in a background thread after startup: register events, then emit service.started."""
    register_events()
    audit_service.emit("service.started", None, actor_type="service")


def _extract_actor_info(request: Request) -> tuple[str | None, str | None, str | None]:
    """Декодирует JWT для получения user_id + username + department_id (только для аудита).

    Возвращает (actor_id, username, department_id). Ошибки игнорируются → (None, None, None).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, None, None
    token = auth[7:]
    try:
        from src.core.security import decode_access_token
        payload = decode_access_token(token)
        return payload.get("sub"), payload.get("username"), payload.get("department_id")
    except Exception:
        return None, None, None


def _extract_client_ip(request: Request) -> str | None:
    """Достаёт IP клиента, учитывая reverse-proxy через X-Forwarded-For."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        # Первый адрес в цепочке — оригинальный клиент
        return xff.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return None


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
