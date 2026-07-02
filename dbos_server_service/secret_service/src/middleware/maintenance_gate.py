"""Maintenance-gate: 503 на все запросы, пока идёт force-перешифровка.

Когда оператор запускает ротацию ключа в режиме `force`, во всём флоте
включается force-флаг (`reencrypt_state.force_active`). Пока он активен, любой
запрос получает `503 REENCRYPT_IN_PROGRESS` + `Retry-After`, чтобы клиент не
читал/не писал секреты, пока хранилище перешифровывается под новый ключ.

Исключения (пропускаются всегда):

* `/health`, `/ready` — k8s liveness/readiness probe'ы. Заблокировать их
  нельзя технически: probe провалится, k8s перезапустит/выведет pod из трафика
  прямо посреди force-окна, дренаж прервётся;
* status-эндпоинты перешифровки (`/migration_status`, `/reencrypt_outbox/status`)
  — по ним UI/оператор следит за прогрессом force-окна.

Регистрируется в `main.py` снаружи audit/slowapi, чтобы поток 503 в
maintenance-окне не порождал audit-amplification.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.services import reencrypt_state_service

# Суффиксы путей, которые gate НЕ блокирует даже в force-окне.
_EXEMPT_SUFFIXES: tuple[str, ...] = (
    "/health",
    "/ready",
    "/migration_status",
    "/reencrypt_outbox/status",
)


def _is_exempt(path: str) -> bool:
    return path.endswith(_EXEMPT_SUFFIXES)


class ReencryptMaintenanceMiddleware(BaseHTTPMiddleware):
    """Отбивает 503 REENCRYPT_IN_PROGRESS, пока активен force-режим."""

    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path):
            return await call_next(request)

        state = await reencrypt_state_service.get_cached_state()
        if not state.force_active:
            return await call_next(request)

        retry_after, eta_seconds = reencrypt_state_service.compute_retry_after(
            remaining=state.remaining, throughput=state.throughput
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "error_code": "REENCRYPT_IN_PROGRESS",
                "message": (
                    "Encryption key re-encryption is in progress; the service is "
                    "temporarily read/write-locked. Retry after the indicated delay."
                ),
                "retry_after": retry_after,
                "eta_seconds": eta_seconds,
                "remaining": state.remaining,
                "request_id": getattr(request.state, "request_id", None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Retry-After": str(retry_after)},
        )
