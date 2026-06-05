"""Proxy логина для Swagger /docs.

`POST /token` — OAuth2 password flow. Проксирует креды в `auth_service` и
возвращает полученный JWT. Только пользователи с `platform_role=loging_admin`
смогут потом использовать admin-эндпоинты loging_service.

Этот эндпоинт прячется от схемы (`include_in_schema=False`) — но Swagger UI
читает его через `tokenUrl` в OAuth2-flow секции (см. `main.custom_openapi`).

Pooled-клиент (`auth_deps._token_proxy_client`) собирается в lifespan'е.
Если он не инициализирован (early import / ad-hoc тесты без TestClient)
— открываем эфемерный `AsyncClient` ровно на один запрос. Sync httpx
нигде не используется.
"""

import logging

import httpx
from fastapi import APIRouter, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends

from src.core.config import get_settings
from src.core.exceptions import AppException, AuthenticationError
from src.dependencies import auth as auth_deps

logger = logging.getLogger(__name__)

router = APIRouter()


_TOKEN_PATH = "/api/auth/v1/token"


@router.post("/token", include_in_schema=False)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    settings = get_settings()
    if not settings.auth_service_url:
        raise AppException(
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="AUTH_SERVICE_NOT_CONFIGURED",
            message="AUTH_SERVICE_URL is not configured",
        )
    data = {"username": form_data.username, "password": form_data.password}
    pooled = auth_deps._token_proxy_client
    try:
        if pooled is not None:
            resp = await pooled.post(_TOKEN_PATH, data=data)
        else:
            # Fallback (ad-hoc): эфемерный AsyncClient на один запрос.
            # read/write общий с introspect'ом, connect отдельно — чтобы
            # залипший handshake не съел read-budget; без явного connect
            # fallback тихо ловил бы залип на полный introspect_timeout.
            # `Limits` симметричны pooled token-proxy клиенту: без них
            # параллельные fallback-обращения (тест/REPL без TestClient под
            # нагрузкой) могли бы открыть N TCP+TLS handshake'ов без cap'а —
            # slowloris-сценарий. Лимит мелкий: fallback — это backstop, не
            # production-hot-path.
            timeout = httpx.Timeout(
                settings.introspect_timeout_seconds,
                connect=settings.introspect_connect_timeout_seconds,
            )
            limits = httpx.Limits(
                max_connections=settings.token_proxy_pool_max_connections,
                max_keepalive_connections=0,
            )
            async with httpx.AsyncClient(timeout=timeout, limits=limits) as ephemeral:
                resp = await ephemeral.post(
                    f"{settings.auth_service_url}{_TOKEN_PATH}",
                    data=data,
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
        #
        # 4xx (`401`/`403`/`422`/`429`) и 5xx (`502`/`503`) от auth_service
        # склеиваются в один 401 INVALID_CREDENTIALS сознательно — Swagger UI
        # читает только body, и пользователь не сможет осмысленно различить
        # «неверный пароль» от «auth_service лежит». Реальная недоступность
        # auth_service сюда не доходит — `httpx`-исключения ловятся выше и
        # дают 503 AUTH_SERVICE_UNREACHABLE; мы здесь только когда сервис
        # ответил HTTP'ом. Логируем upstream-статус, чтобы оператор всё-таки
        # мог отличить «лежит» от «не угадал пароль» по логам.
        logger.warning(
            "auth_service /token proxy: upstream returned %d, mapping to 401",
            resp.status_code,
        )
        raise AuthenticationError(
            error_code="INVALID_CREDENTIALS",
            message="Invalid credentials",
        )
    body = resp.json()
    return {"access_token": body["access_token"], "token_type": "bearer"}
