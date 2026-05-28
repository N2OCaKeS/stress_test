"""SlowAPI Limiter, вынесен из `src.main` ради разрыва цикла.

`api/v1/endpoints/events.py` и `api/v1/endpoints/services.py` декорируют
свои handler'ы через `@limiter.limit(...)`. Если limiter лежит в
`src.main`, то FastAPI-роутеры импортируют `src.main` ещё до того, как
тот успевает собрать `app` — endpoints/* подтягиваются из
`src.api.router`, который импортируется ровно во время сборки `app`. Это
циклическая зависимость, которую раньше затыкали `noqa: E402` import'ом
после module-level statement'а.

Здесь limiter инициализируется один раз; и `main`, и endpoints импортят
его из нейтрального модуля.

Health/token-эндпоинты получают unique-per-request ключ, чтобы никогда
не коллизить с общим bucket'ом per-IP клиентов (k8s readiness иначе
насыщала бы window).
"""

import uuid

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import get_settings


_RATE_LIMIT_EXEMPT_PATHS = {
    "/api/logging/v1/health",
    "/api/logging/v1/ready",
    "/api/logging/v1/token",
}


def _rate_limit_key(request: Request) -> str:
    """Дефолтная key-функция SlowAPI-лимитера.

    Для health/token — uuid-ключ (никогда не копит counts в общем
    bucket'е). Для остального — client IP. Ingest-канал (`POST /events`)
    и batch-канал (`POST /services/{service}/events`) переопределяют
    key_func прямо в декораторах на per-service identity — за k8s
    ingress общий per-IP bucket позволил бы одному сервису выжать
    бюджет остальных.
    """
    if request.url.path in _RATE_LIMIT_EXEMPT_PATHS:
        return f"exempt:{uuid.uuid4().hex}"
    return get_remote_address(request)


# `headers_enabled` по умолчанию off — `X-RateLimit-Remaining` утекает
# атакующему live feedback его counter'а, и тот burst'ит ровно под
# лимит. Включается через `RATE_LIMIT_HEADERS_ENABLED=true` для отладки.
limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[],
    headers_enabled=get_settings().rate_limit_headers_enabled,
)
