"""Guard для `/internal/*` lifecycle-эндпоинтов.

Внутренние вызовы приходят от auth_service / account_admin handler'а через
shared bearer-секрет, а не через user JWT/introspect — это служебная
плоскость, и нам нет смысла гонять обратный introspect к auth_service'у.

Dual-mode: если в env задан `SERVICE_API_KEYS` (per-caller map) — выбираем
ключ по заголовку `X-Service-Identity`. Иначе — legacy single key
`SERVICE_API_KEY`. Сравнение — через `secrets.compare_digest`.
"""

from __future__ import annotations

import logging
import secrets as _secrets

from fastapi import Request

from src.core.config import get_settings
from src.core.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

__all__ = [
    "require_internal_caller",
    "require_caller_identity",
]


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


def require_internal_caller(request: Request) -> str | None:
    """401 INTERNAL_AUTH_REQUIRED при отсутствии валидного bearer'а.

    Возвращает имя caller'а (из `X-Service-Identity`), если задан
    `SERVICE_API_KEYS`; в legacy-режиме — содержимое заголовка как
    подсказку (может быть None). В обоих случаях request.state.caller
    проставляется, чтобы handler мог логировать.
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthenticationError(
            error_code="INTERNAL_AUTH_REQUIRED",
            message="Bearer token required for /internal calls",
        )

    settings = get_settings()
    raw_identity = request.headers.get("X-Service-Identity")
    caller = raw_identity.strip() if raw_identity else None
    if caller == "":
        caller = None

    if settings.service_api_keys:
        if caller is None:
            raise AuthenticationError(
                error_code="INTERNAL_AUTH_REQUIRED",
                message="X-Service-Identity header required when SERVICE_API_KEYS is configured",
            )
        expected = settings.service_api_keys.get(caller)
        if expected is None or not _secrets.compare_digest(token, expected):
            raise AuthenticationError(
                error_code="INTERNAL_AUTH_REQUIRED",
                message="Invalid internal API key",
            )
        request.state.caller = caller
        return caller

    # Legacy single shared secret. Header опционален — просто стэшим, чтобы
    # audit видел кто звонил, если caller добровольно его прислал.
    expected = settings.service_api_key
    if not expected or not _secrets.compare_digest(token, expected):
        raise AuthenticationError(
            error_code="INTERNAL_AUTH_REQUIRED",
            message="Invalid internal API key",
        )
    request.state.caller = caller
    return caller


def require_caller_identity(name: str):
    """Фабрика guard'а, который дополнительно требует совпадения `X-Service-Identity`.

    Применяется, если конкретный endpoint обслуживает строго один caller —
    например, lifecycle-события приходят только от auth_service. Если в env
    `SERVICE_API_KEYS` пуст (legacy), а caller не назвался — отбиваем 401.
    """

    def _guard(request: Request) -> None:
        require_internal_caller(request)
        if getattr(request.state, "caller", None) != name:
            raise AuthenticationError(
                error_code="INTERNAL_AUTH_REQUIRED",
                message=f"Endpoint accepts calls only from {name!r}",
            )

    return _guard
