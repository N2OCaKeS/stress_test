"""HTTPS-guard middleware: в production/staging отбивает cleartext-HTTP с 403.

Зеркалит `server_service/src/middleware/https_guard.py` и
`loging_service/src/core/https_guard.py`. Единая модель угроз: за ingress'ом
всегда стоит TLS-терминатор, любой http-запрос до пода — либо mis-config,
либо попытка обхода. secret_service хранит и отдаёт расшифрованные секреты
(reveal); cleartext-roundtrip — потенциальный leak.

Health/ready пропускаются (k8s probe ходит на pod-network http). В dev/test/local
middleware выключен — devcontainer и тесты идут без TLS.

Регистрируется в `main.py` ПОСЛЕ SecurityHeadersMiddleware → outermost-слой:
cleartext-запрос отбивается ДО rate-limit'а, audit middleware, introspect'а.
Никакого audit-amplification на http-флуд, никакого расхода introspect-pool
на запросы, которые мы и так отвергнем.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.constants import HEALTH_PATHS

HTTPS_REQUIRED_ENVS: frozenset[str] = frozenset({"production", "staging"})


def _is_request_https(request: Request) -> bool:
    """True если запрос пришёл по https напрямую или через TLS-терминатор.

    Доверяем правому-крайнему токену `X-Forwarded-Proto`: он от closest-hop'а
    (нашего ingress'а), а всё, что левее — от внешних прокси, которым доверять
    нельзя. Если ingress сваливает входящий XFP в список, мы хотя бы не
    принимаем чужой `https` в начале цепочки.
    """
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").strip().lower()
    if not forwarded_proto:
        return False
    last = forwarded_proto.rsplit(",", 1)[-1].strip()
    return last == "https"


def _build_https_required_response(request: Request) -> JSONResponse:
    """403 envelope с error_code=HTTPS_REQUIRED."""
    return JSONResponse(
        status_code=403,
        content={
            "error": "forbidden",
            "error_code": "HTTPS_REQUIRED",
            "message": (
                "Cleartext HTTP requests are rejected in this environment; "
                "use https:// (TLS must be terminated by the ingress)."
            ),
            "details": {"scheme": request.url.scheme},
            "request_id": getattr(request.state, "request_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


class HTTPSRequiredMiddleware(BaseHTTPMiddleware):
    """В production/staging отбивает cleartext-HTTP с 403 HTTPS_REQUIRED."""

    def __init__(self, app, *, app_env: str):
        super().__init__(app)
        import os as _os_https_guard
        # `DBOS_HTTPS_GUARD_DISABLED=true|1|yes` явно выключает middleware
        # для intra-cluster-only deploy'я (NetworkPolicy default-deny
        # держит границу namespace'а — внешний http не достучится).
        _disabled = _os_https_guard.environ.get(
            "DBOS_HTTPS_GUARD_DISABLED", ""
        ).strip().lower() in {"true", "1", "yes"}
        self._enabled = (
            (app_env.lower() in HTTPS_REQUIRED_ENVS) and not _disabled
        )

    async def dispatch(self, request: Request, call_next):
        if not self._enabled:
            return await call_next(request)
        if request.url.path in HEALTH_PATHS:
            return await call_next(request)
        if _is_request_https(request):
            return await call_next(request)
        return _build_https_required_response(request)
