"""Security headers middleware."""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# Пути, на которых CSP ослабляется под Swagger UI: бандл грузится скриптом и
# стилем, поэтому строгий `default-src 'none'` отдаёт белую страницу.
_SWAGGER_CSP_PATHS = frozenset({"/docs", "/docs/oauth2-redirect"})

_SWAGGER_CDN = "https://cdn.jsdelivr.net"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security headers на всех ответах.

    Зеркалит логику auth_service/loging_service/server_service/secret_service.
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
