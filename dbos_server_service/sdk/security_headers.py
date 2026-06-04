"""SecurityHeadersMiddleware — общий набор защитных HTTP-заголовков.

Канонический источник для копи-паста между сервисами. Эталон — superset
из всех трёх сервисных копий (включает Permissions-Policy, исторически
жившую только в auth_service).

Где сейчас дублируется этот middleware:
    auth_service/src/main.py     — полный набор (включая Permissions-Policy)
    loging_service/src/main.py   — полный набор
    server_service/src/main.py   — полный набор

Назначение заголовков:

* `X-Frame-Options: DENY` + `Content-Security-Policy: frame-ancestors 'none'`
  парные защиты от clickjacking'а.
* `X-Content-Type-Options: nosniff` — отключает MIME-sniffing браузером.
* `Referrer-Policy: strict-origin-when-cross-origin` — не утекаем полный
  URL во внешние ресурсы.
* `Content-Security-Policy: default-src 'none'` — для JSON-API запрещаем
  всё. Swagger UI на /docs (dev-only) тянет свои inline-script'ы, но
  `frame-ancestors 'none'` парный с X-Frame-Options.
* `Permissions-Policy` — обнуляем sensor-API (geolocation, microphone и т.д.)
  Браузерных клиентов у нас нет, но если когда-то появится — оно
  заведомо не должно иметь доступа к камере/геолокации/USB.
* `Strict-Transport-Security` (HSTS) — опционально через `hsts_enabled`.
  За http-фронтом включать нельзя: rebound отвалится. Включать только
  когда сервис стоит за TLS-терминатором с https в обе стороны.

Использование (в `main.create_application`):

    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.security_hsts_enabled,
    )

Регистрировать ПОСЛЕДНИМ из `add_middleware` — Starlette стакает в обратном
порядке, и middleware становится **outermost**: заголовки навешиваются на
все ответы, включая 4xx/5xx из exception-handler'ов.
"""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security headers (HSTS опционально, X-Frame, CSP, и т.д.).

    HSTS включается только при `hsts_enabled=True` — за http-фронтом он
    сломает rebound. Остальные заголовки безопасны при любой конфигурации.
    """

    def __init__(self, app, *, hsts_enabled: bool) -> None:
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
