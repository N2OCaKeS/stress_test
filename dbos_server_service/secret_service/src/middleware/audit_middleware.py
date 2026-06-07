"""Inner-most middleware: на 4xx/5xx ответе пишет http.* audit-event.

401/403 → `http.unauthorized` (WARNING).
Прочие 4xx (кроме 501) → `http.client_error` (INFO).
5xx → `http.server_error` (ERROR).
501 и health-paths пропускаются ранним return'ом — 501 — заглушка endpoint'а,
не действие пользователя; health/ready режется ещё outermost'ом, но дублирующий
guard здесь — defense-in-depth.
"""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.constants import HEALTH_PATHS
from src.services import audit_service


class AuditAccessMiddleware(BaseHTTPMiddleware):
    """Innermost middleware: эмитит http.* событие для 4xx/5xx ответов."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in HEALTH_PATHS:
            return response

        status_code = response.status_code
        if status_code < 400:
            return response

        # 501 NOT_IMPLEMENTED — заглушка endpoint'а, не действие юзера.
        # Authenticated insider циклом по stub'ам генерил бы лавину
        # `http.server_error` (audit-amplification). Реальные 5xx — баги,
        # БД-сбои — по-прежнему пишутся.
        if status_code == 501:
            return response

        details = {"method": request.method, "path": path, "status_code": status_code}

        if status_code in (401, 403):
            action, emit_status = "http.unauthorized", "failure"
        elif status_code < 500:
            action, emit_status = "http.client_error", "failure"
        else:
            action, emit_status = "http.server_error", "failure"

        # Контекст уже выставлен внешним middleware — emit() сам подхватит
        # ip/ua. Не оборачиваем в `asyncio.to_thread`: emit детектит running
        # loop и шедулит через pooled `_audit_client`. С `to_thread` — running
        # loop'а нет, emit бы попадал в sync-fallback мимо pool'а.
        audit_service.emit(
            action,
            status=emit_status,
            allowed=False,
            details=details,
        )
        return response
