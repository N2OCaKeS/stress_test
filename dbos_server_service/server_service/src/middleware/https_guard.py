"""HTTPS-guard middleware: в production/staging отбивает cleartext-HTTP.

### Зачем

server_service хранит и отдаёт секреты (IPMI-пароли, ipmi-токены, decrypted
credentials для воркера через internal endpoints, `server_account.password`).
Любой cleartext-запрос в боевом окружении — потенциальный leak. Frontend
(ingress / service-mesh) обязан терминировать TLS до подов, но защита
``defense-in-depth``: если из-за ошибки конфига запрос всё-таки доехал до
сервиса по http, мы должны его отбить, а не молча отдать данные.

Логика:

* В ``app_env ∈ {production, staging}`` middleware проверяет схему запроса.
* Если запрос пришёл по http (``request.url.scheme != "https"`` и
  ``X-Forwarded-Proto`` не равен ``"https"``) — возвращается 403 с
  ``error_code=HTTPS_REQUIRED``.
* Health/ready пропускаются всегда: k8s probe ходит на pod-network ``http``
  и не должен валиться 403.
* В dev/test/local middleware вообще выключен — uvicorn в devcontainer
  поднимается на http, тесты гоняются через ASGI-transport без TLS.

### Почему 403, а не 426 Upgrade Required

426 семантически точнее, но клиенты server_service — внутренние сервисы
(server_worker, web_settings, cli, allta_app). Им нечем «upgrade'нуться»
из http в https на лету — нужна перенастройка конфига и redeploy.
403 для них яснее: «доступ запрещён по политике», и envelope-формат
тот же, что у остальных ошибок сервиса.

### Порядок в стеке

Регистрируется как ``add_middleware`` ПОСЛЕ ``SecurityHeadersMiddleware``
(то есть становится самым outermost'ом). Любой cleartext-запрос
отбивается ДО rate-limit'а, audit_access, platform-admin guard'а —
никакого audit-amplification на http-флуд, никакого расхода
introspect-pool на запросы, которые мы и так отвергнем.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Окружения, в которых требуется HTTPS. Зеркалит `_HTTPS_REQUIRED_ENVS` из
# `server_worker/src/core/config.py` и список из `_require_https_*_in_prod`
# валидаторов `server_service/src/core/config.py`.
HTTPS_REQUIRED_ENVS: frozenset[str] = frozenset({"production", "staging"})

# Health-пути, которые middleware пропускает даже в production: k8s liveness
# и readiness probe ходят внутри pod-network по http, и 403 на них приведёт
# к рестарту pod'а в loop'е.
_HEALTH_PATHS: frozenset[str] = frozenset({
    "/api/server/v1/health",
    "/api/server/v1/ready",
})


def _is_request_https(request: Request) -> bool:
    """True если запрос пришёл по https (напрямую или через TLS-терминатор).

    Источников два:

    * ``request.url.scheme`` — выставляется uvicorn'ом из ASGI-scope. В
      контейнере за TLS-терминатором всегда ``http`` (терминатор открыл
      TLS до нас).
    * ``X-Forwarded-Proto`` — стандартный header'а от ingress'а / mesh'а
      (envoy, nginx-ingress, traefik). Если он ``https`` — клиент пришёл
      по TLS, мы за прокси.

    Доверять можно только **последнему** значению в цепочке XFP: оно от
    closest-hop'а (нашего ingress'а), а всё, что левее, — приходит от
    клиента/внешних прокси, которые могут это значение подделать. Если
    ingress не настроен правильно перетирать заголовок целиком, ставя
    единственное значение, и сваливает входящий XFP в список — мы хотя
    бы не доверяем чужому ``https`` в начале списка.
    """
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").strip().lower()
    if not forwarded_proto:
        return False
    # XFP-список вида `https, http` или единичное значение `https`. Берём
    # правый-крайний токен — он от closest-trusted-proxy (нашего ingress'а).
    last = forwarded_proto.rsplit(",", 1)[-1].strip()
    return last == "https"


def _build_https_required_response(request: Request) -> JSONResponse:
    """403 envelope с error_code=HTTPS_REQUIRED.

    Shape совместим с ``app_exception_handler`` из ``main.py`` —
    те же поля (``error``/``error_code``/``message``/``details``/
    ``request_id``/``timestamp``).
    """
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
    """ASGI middleware: в prod/staging отбивает cleartext-HTTP с 403.

    Параметры:

    * ``app_env`` — текущее окружение, обычно ``settings.app_env``. Если
      оно не в ``HTTPS_REQUIRED_ENVS``, middleware выключен и просто
      пропускает все запросы.

    Поведение для production/staging:

    * health/ready — всегда проходят (k8s probe на pod-network http).
    * Всё остальное — должно быть https (request.url.scheme или
      X-Forwarded-Proto). Иначе 403 ``HTTPS_REQUIRED``.
    """

    def __init__(self, app, *, app_env: str):
        super().__init__(app)
        self._enabled = app_env.lower() in HTTPS_REQUIRED_ENVS

    async def dispatch(self, request: Request, call_next):
        if not self._enabled:
            return await call_next(request)
        if request.url.path in _HEALTH_PATHS:
            return await call_next(request)
        if _is_request_https(request):
            return await call_next(request)
        return _build_https_required_response(request)
