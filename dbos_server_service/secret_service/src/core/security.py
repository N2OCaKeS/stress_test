"""Security headers middleware. Шифрование/JWT/Argon2 живут в отдельных модулях."""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security headers на всех ответах.

    HSTS включается только когда `hsts_enabled=True` — за http-фронтом он
    сломает rebound у клиентов. X-Frame-Options/X-Content-Type-Options/CSP/
    Referrer-Policy/Permissions-Policy ставятся всегда. Зеркалит логику в
    auth_service / loging_service / server_service.
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
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
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
