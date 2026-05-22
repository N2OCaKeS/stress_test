"""Proxy логина для Swagger /docs.

`POST /token` — OAuth2 password flow. Проксирует креды в `auth_service` и
возвращает полученный JWT. Только пользователи с `platform_role=loging_admin`
смогут потом использовать admin-эндпоинты loging_service.

Этот эндпоинт прячется от схемы (`include_in_schema=False`) — но Swagger UI
читает его через `tokenUrl` в OAuth2-flow секции (см. `main.custom_openapi`).
"""

import logging

import httpx
from fastapi import APIRouter, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends

from src.core.config import get_settings
from src.core.exceptions import AppException, AuthenticationError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/token", include_in_schema=False)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    settings = get_settings()
    if not settings.auth_service_url:
        raise AppException(
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="AUTH_SERVICE_NOT_CONFIGURED",
            message="AUTH_SERVICE_URL is not configured",
        )
    try:
        resp = httpx.post(
            f"{settings.auth_service_url}/api/auth/v1/token",
            data={"username": form_data.username, "password": form_data.password},
            timeout=5.0,
        )
    except Exception as exc:
        # Детальная ошибка (включая внутренний hostname / URL, который
        # `ConnectError` / `ReadError` / `RemoteProtocolError` кладут в repr)
        # идёт ТОЛЬКО в логи. User-facing envelope обязан остаться generic —
        # см. TODO «loging proxy /token except Exception кладёт str(exc) в
        # detail»: `httpx`-исключения тащат `request.url`, который слил бы
        # cluster-internal auth_service URL.
        logger.error("auth_service /token proxy failed: %s", exc, exc_info=True)
        raise AppException(
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="AUTH_SERVICE_UNREACHABLE",
            message="Authentication service is temporarily unavailable",
        )
    if resp.status_code != 200:
        # Один envelope для всего сервиса. Bearer-challenge header'а нет —
        # `AppException`-handler не умеет custom headers, и для browser-flow
        # /token это и не нужно: Swagger UI читает body, не `WWW-Authenticate`.
        raise AuthenticationError(
            error_code="INVALID_CREDENTIALS",
            message="Invalid credentials",
        )
    data = resp.json()
    return {"access_token": data["access_token"], "token_type": "bearer"}
