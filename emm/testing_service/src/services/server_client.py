"""Исходящие вызовы в server_service.

Пока нужен одному потребителю — резолверам `choices_source`
(`dynamic:os_versions` / `dynamic:kernels`, см. `services/choices.py`),
которые спрашивают у server_service живой каталог OS-версий и список ядер
конкретной версии. Волна очереди добавит сюда prepare-for-test,
acquire/release-for-service и прокси test-credentials.

Аутентификация — тот же shared-secret паттерн, что у audit-emit и у
callback'ов server_service: `Authorization: Bearer <SERVER_SERVICE_API_KEY>`
плюс `X-Service-Identity: testing_service`.

Важно про сам ключ: каталожные пути `/os-versions` на стороне server_service
закрыты не whitelist'ом сервисов, а обычным introspect'ом («любой
аутентифицированный актор»). Значит в `SERVER_SERVICE_API_KEY` должен лежать
токен, который auth_service считает валидным bearer'ом — бот-токен
testing_service (`dbos_bot_…`) либо PAT. Чистый s2s-ключ из
`SERVICE_API_KEYS` server_service'а тут не подойдёт: internal-роута под
чтение каталога у него нет.

Пул не заводим: вызовы редкие (открытие выпадающего списка в конструкторе),
per-call клиент дешевле долгоживущего сокет-пула.

Карточка одного сервера (`get_server`) — отдельный случай. `GET /servers/{id}`
у server_service, в отличие от каталогов выше, гейтит видимость по
department_id caller'а (`_ensure_visible` в `server_service/src/services/
server.py`): виден сервер своего отдела либо тот, на который есть инстанс-
грант, иначе — 404, даже если сервер существует. Бот-токен testing_service
живёт в системном отделе и под это правило не подпадает — с ним каждый чужой
отдел выглядел бы как 404. Поэтому `get_server` пробрасывает bearer
вызывающего как есть (см. `dependencies.auth.BearerToken`), а не сервисный
ключ — server_service применит те же правила видимости, что и при прямом
запросе пользователя.
"""

from __future__ import annotations

import logging

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME
from src.core.exceptions import AuthorizationError, NotFoundError, ServiceUnavailableError
from src.core.http import bearer_header

logger = logging.getLogger("testing_service.server_client")

_OS_VERSIONS_PATH = "/api/server/v1/os-versions"
_SERVERS_PATH = "/api/server/v1/servers"

# Каталог версий — десятки записей; берём страницу с запасом, пагинацию в
# UI-списке значений разводить незачем.
_OS_VERSIONS_PAGE_LIMIT = 500


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def is_configured() -> bool:
    """Настроен ли канал (URL + ключ)."""
    settings = get_settings()
    return bool(settings.server_service_url and settings.server_service_api_key)


async def _send_get(path: str, params: dict | None, headers: dict[str, str]) -> httpx.Response:
    """Сырой GET к server_service. Сетевые сбои → 503, статус-код разбирает caller."""
    settings = get_settings()
    base = (settings.server_service_url or "").rstrip("/")
    if not base:
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_NOT_CONFIGURED",
            message="SERVER_SERVICE_URL is not configured",
        )
    async with build_client(settings.server_request_timeout_seconds) as client:
        try:
            return await client.get(f"{base}{path}", params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise ServiceUnavailableError(
                error_code="SERVER_SERVICE_TIMEOUT",
                message="server_service did not respond in time",
            ) from exc
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="SERVER_SERVICE_UNREACHABLE",
                message=f"Unable to reach server_service: {type(exc).__name__}",
            ) from exc


def _parse_json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_ERROR",
            message="server_service returned a non-JSON body",
        ) from exc


async def _get(path: str, params: dict | None = None) -> dict:
    """GET к server_service с s2s bot-токеном сервиса. Под department-agnostic каталоги."""
    settings = get_settings()
    api_key = settings.server_service_api_key
    if not api_key:
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_NOT_CONFIGURED",
            message="SERVER_SERVICE_API_KEY is not configured",
        )
    headers = {**bearer_header(api_key), "X-Service-Identity": SERVICE_NAME}
    response = await _send_get(path, params, headers)

    if response.status_code == 404:
        raise NotFoundError(
            error_code="SERVER_SERVICE_OBJECT_NOT_FOUND",
            message="server_service returned 404 for the requested object",
            details={"path": path},
        )
    if response.status_code >= 300:
        logger.warning(
            "server_service ответил %s на %s", response.status_code, path,
        )
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_ERROR",
            message=f"server_service returned {response.status_code}",
        )
    return _parse_json(response)


async def _get_passthrough(path: str, bearer_token: str) -> dict:
    """GET к server_service с bearer'ом ВЫЗЫВАЮЩЕГО вместо сервисного ключа.

    Используется там, где server_service гейтит видимость по department_id
    caller'а — см. module docstring.
    """
    response = await _send_get(path, None, bearer_header(bearer_token))

    if response.status_code == 404:
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found or not visible to the caller",
            details={"path": path},
        )
    if response.status_code == 403:
        raise AuthorizationError(
            error_code="SERVER_ACCESS_DENIED",
            message="server_service denied access to this server",
            details={"path": path},
        )
    if response.status_code >= 300:
        logger.warning(
            "server_service (pass-through) ответил %s на %s", response.status_code, path,
        )
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_ERROR",
            message=f"server_service returned {response.status_code}",
        )
    return _parse_json(response)


async def list_os_versions() -> list[dict]:
    """Каталог OS-версий целиком. Возвращает сырые карточки server_service'а."""
    body = await _get(_OS_VERSIONS_PATH, params={"limit": _OS_VERSIONS_PAGE_LIMIT})
    items = body.get("items")
    return list(items) if isinstance(items, list) else []


async def get_os_version(os_version_id: str) -> dict:
    """Карточка одной OS-версии. 404 у server_service → NotFoundError."""
    return await _get(f"{_OS_VERSIONS_PATH}/{os_version_id}")


async def get_server(bearer_token: str, server_id: str) -> dict:
    """Карточка сервера/ВМ, с pass-through bearer'ом вызывающего (§4 плана миграции).

    `bearer_token` — сырой токен из заголовка входящего HTTP-запроса, не
    `SERVER_SERVICE_API_KEY`: server_service решает видимость сервера по
    department_id держателя этого токена. 404 — сервер не существует либо не
    виден caller'у (server_service не различает эти случаи специально, чтобы
    не палить чужие server_id). 403 — department caller'а вовсе не подключён
    к server_service.
    """
    return await _get_passthrough(f"{_SERVERS_PATH}/{server_id}", bearer_token)
