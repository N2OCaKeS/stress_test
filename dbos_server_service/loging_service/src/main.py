"""Application entrypoint for logging_service."""

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from src.api.router import api_router
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.core.logging import configure_logging


def create_application() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        debug=settings.app_debug,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    _HEALTH_PATHS = {"/api/logging/v1/health", "/api/logging/v1/ready"}

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # Эндпоинты где события не нужны (служебные / рекурсивные)
    _SKIP_AUDIT_PATHS = {
        "/api/logging/v1/health",
        "/api/logging/v1/ready",
        "/api/logging/v1/token",
    }
    # POST на эти префиксы — входящие от сервисов, не аудитируем (избегаем рекурсии)
    _INGEST_PREFIXES = (
        "/api/logging/v1/events",
        "/api/logging/v1/services/",  # POST /services/{svc}/events
    )

    @app.middleware("http")
    async def audit_access(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        status_code = response.status_code

        # Пропускаем служебные пути
        if path in _SKIP_AUDIT_PATHS:
            return response

        # Пропускаем POST от сервисов (ingest события, регистрация событий)
        if request.method == "POST" and path.startswith(_INGEST_PREFIXES):
            return response

        identity = getattr(request.state, "auth_identity", None)
        actor_id = identity.get("user_id") if identity else None
        username = identity.get("username") if identity else None
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

        asyncio.ensure_future(asyncio.to_thread(
            _emit_audit, action, actor_id, username, emit_status, allowed, request_id, details
        ))
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
                "details": {"errors": jsonable_encoder(exc.errors())},
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    app.include_router(api_router, prefix="/api")

    @app.on_event("startup")
    def start_retention_cleanup() -> None:
        t = threading.Thread(target=_retention_loop, daemon=True)
        t.start()

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)

        # Replace auto-generated HTTPBearer with our named schemes
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes.pop("HTTPBearer", None)
        schemes["ServiceAPIKey"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Paste SERVICE_API_KEY directly (service-to-service auth)",
        }
        schemes["OAuth2Password"] = {
            "type": "oauth2",
            "description": "Login with LOGGING_ADMIN_USERNAME + LOGGING_ADMIN_PASSWORD",
            "flows": {
                "password": {
                    "tokenUrl": "/api/logging/v1/token",
                    "scopes": {},
                }
            },
        }

        # Replace endpoint-level HTTPBearer references so both schemes are accepted
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


def _retention_loop() -> None:
    """Background thread: apply retention policy once per day at 00:00 MSK (UTC+3)."""
    from datetime import date
    from zoneinfo import ZoneInfo
    _MSK = ZoneInfo("Europe/Moscow")
    last_run: date | None = None

    while True:
        now_msk = datetime.now(_MSK)
        today = now_msk.date()

        if last_run != today and now_msk.hour == 0:
            last_run = today
            try:
                from src.db.session import SessionLocal
                from src.repositories.retention_policies import apply_active
                db = SessionLocal()
                try:
                    deleted = apply_active(db)
                    logger.info("Retention cleanup [%s MSK]: deleted %d events", today, deleted)
                finally:
                    db.close()
            except Exception as exc:
                logger.error("Retention cleanup failed: %s", exc)

        # Sleep until the next minute check
        time.sleep(60)


def _action_for_path(method: str, path: str) -> str:
    """Определяет имя action для успешного обращения к admin-эндпоинту loging_service."""
    if "/rules" in path:
        return "logging.rules_read" if method == "GET" else "logging.rules_write"
    if "/services" in path:
        return "logging.services_read"
    if "/events" in path:
        return "logging.events_queried"
    return "logging.admin_access"


def _emit_audit(
    action: str,
    actor_id: str | None,
    username: str | None,
    emit_status: str,
    allowed: bool,
    request_id: str | None,
    details: dict,
) -> None:
    """Записывает событие loging_service напрямую в БД — всегда минуя правила SUPPRESS.

    Это гарантирует что аудит loging_service нельзя скрыть никакими правилами.
    """
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
                actor_type="user" if actor_id else "anonymous",
                username=username,
                status=emit_status,
                allowed=allowed,
                request_id=request_id,
                details=details,
            ),
        )
    except Exception:
        pass
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
