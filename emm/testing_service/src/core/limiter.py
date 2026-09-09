"""SlowAPI Limiter, вынесен из `src.main` ради разрыва цикла импортов.

Endpoint-модули будущих волн вешают `@limiter.limit(...)` декораторы поверх
глобального лимита из `settings.slowapi_rate_limit`. `app.state.limiter`
выставляется в `create_application()` — slowapi-декораторы ищут limiter там.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import get_settings

_settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_settings.slowapi_rate_limit],
    headers_enabled=False,
)
