"""Исходящие вызовы в server_service.

Три группы функций, три разных auth-паттерна:

* `list_os_versions`/`get_os_version` — каталоги, `_get` с бот-токеном
  сервиса (`SERVER_SERVICE_API_KEY`, валиден для auth_service introspect).
* `get_server`/`get_test_credentials` — pass-through bearer'а вызывающего
  (`_get_passthrough`): server_service гейтит видимость/`server_service.admin`
  по department_id держателя токена, не по факту, что testing_service вообще
  аутентифицирован.
* `acquire_for_service`/`release_for_service`/`release_for_service_as_done`/
  `set_service_status`/`start_prepare_for_test`/`get_connection_info` —
  канал брони/подготовки (`_post_internal`, `SERVER_SERVICE_INTERNAL_API_KEY`):
  чистый shared-secret под explicit-whitelist internal-эндпоинты, без
  пользовательской identity.

Аутентификация каталогов — тот же shared-secret паттерн, что у audit-emit и у
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
from dataclasses import dataclass

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)
from src.core.http import bearer_header

logger = logging.getLogger("testing_service.server_client")

_OS_VERSIONS_PATH = "/api/server/v1/os-versions"
_SERVERS_PATH = "/api/server/v1/servers"
_INTERNAL_SERVERS_PATH = "/api/server/v1/internal/servers"

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


async def _send_post(path: str, json_body: dict, headers: dict[str, str], *, timeout: float | None = None) -> httpx.Response:
    """Сырой POST к server_service. Сетевые сбои → 503, статус-код разбирает caller."""
    settings = get_settings()
    base = (settings.server_service_url or "").rstrip("/")
    if not base:
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_NOT_CONFIGURED",
            message="SERVER_SERVICE_URL is not configured",
        )
    async with build_client(timeout or settings.server_request_timeout_seconds) as client:
        try:
            return await client.post(f"{base}{path}", json=json_body, headers=headers)
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


def _parse_json_or_empty(response: httpx.Response) -> dict:
    """Как `_parse_json`, но не падает на не-JSON теле — под разбор ошибок."""
    try:
        return response.json()
    except ValueError:
        return {}


def _parse_json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_ERROR",
            message="server_service returned a non-JSON body",
        ) from exc


async def _get(path: str, params: dict | None = None, *, refresh: bool = False) -> dict:
    """GET к server_service с s2s bot-токеном сервиса. Под department-agnostic каталоги."""
    settings = get_settings()
    api_key = settings.server_service_api_key
    if not api_key:
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_NOT_CONFIGURED",
            message="SERVER_SERVICE_API_KEY is not configured",
        )
    headers = {**bearer_header(api_key), "X-Service-Identity": SERVICE_NAME}
    response = await _send_post(path, {}, headers, timeout=settings.os_kernel_discovery_timeout_seconds) if refresh else await _send_get(path, params, headers)

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


async def find_os_version_by_name(name: str) -> dict | None:
    """Карточка OS-версии по человекочитаемому имени (`"1.8.5.46"`), не по id.

    Легаси-потребители (телеграм-бот, `available-kernels-from-<rc>`) всегда
    оперировали именно версией, а не внутренним `osv_<uuid>` каталога —
    каталог версий небольшой, полное сканирование дешевле отдельной ручки
    поиска по имени на стороне server_service.
    """
    for version in await list_os_versions():
        if str(version.get("name")) == name:
            return version
    return None


@dataclass(frozen=True)
class OsVersionInfo:
    """Человеческие атрибуты OS-версии, нужные testing_service за пределами
    самого `os_version_id` — один `GET` вместо нескольких на каждый нужный
    признак."""

    name: str
    is_urgent_update: bool
    rc_number: str | None


async def resolve_os_version_info(os_version_id: str) -> OsVersionInfo:
    """`osv_<hex>` → `(имя, is_urgent_update, номер РЦ)`.

    `test_runs.os_version_id`/`stp_test_runs.os_version_id` хранят внутренний
    id каталога, а заголовки Confluence, имена и папки Zephyr строятся из
    самого номера РЦ — путать их нельзя, иначе наружу уходит `osv_3f2a…`
    вместо `1.8.5.46`. Исключение (server_service недоступен/версия удалена)
    наружу НЕ гасится: подставить id вместо версии значит опубликовать
    заведомо неверный заголовок, лучше видимый `status=failed`.

    `rc_number` (легаси `"RC3"`) — ручная метка, которую владелец выставляет
    на самой OS-версии в `server_service`; может отсутствовать (`None`), пока
    не проставлена.
    """
    version = await get_os_version(os_version_id)
    name = str(version.get("name") or "").strip()
    if not name:
        raise ServiceUnavailableError(
            error_code="OS_VERSION_NAME_MISSING",
            message=f"server_service returned no name for os_version {os_version_id}",
        )
    rc_number = str(version.get("rc_number") or "").strip() or None
    return OsVersionInfo(
        name=name, is_urgent_update=bool(version.get("is_urgent_update", False)), rc_number=rc_number,
    )


async def resolve_os_version_name(os_version_id: str) -> str:
    """`osv_<hex>` → человеческая строка версии (`"1.8.5.46"`). См. `resolve_os_version_info`."""
    return (await resolve_os_version_info(os_version_id)).name


def _internal_headers() -> dict[str, str]:
    """Заголовки для канала брони/подготовки — `SERVER_SERVICE_INTERNAL_API_KEY`.

    Отдельный ключ от `_get`'ового `SERVER_SERVICE_API_KEY`: этот — чистый
    shared-secret под explicit-whitelist internal-эндпоинты server_service
    (`acquire-for-service`/`release-for-service`/`service-status`/
    `prepare-for-test`/`connection-info`), а не бот-токен для introspect'а.
    """
    settings = get_settings()
    api_key = settings.server_service_internal_api_key
    if not api_key:
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_NOT_CONFIGURED",
            message="SERVER_SERVICE_INTERNAL_API_KEY is not configured",
        )
    return {**bearer_header(api_key), "X-Service-Identity": SERVICE_NAME}


def is_internal_channel_configured() -> bool:
    """Настроен ли канал брони/подготовки (URL + internal-ключ)."""
    settings = get_settings()
    return bool(settings.server_service_url and settings.server_service_internal_api_key)


async def _post_internal(path: str, body: dict) -> dict:
    """POST на internal-канал брони/подготовки. 409 → ConflictError с error_code
    server_service'а — вызывающий код (services/queue.py) различает
    `SERVER_NOT_BUSY` (можно fallback на acquire) от прочих кодов (пробрасывать)."""
    headers = _internal_headers()
    response = await _send_post(path, body, headers)

    if response.status_code == 404:
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="server_service returned 404 for the requested server",
            details={"path": path},
        )
    if response.status_code == 409:
        payload = _parse_json_or_empty(response)
        raise ConflictError(
            error_code=payload.get("error_code") or "SERVER_SERVICE_CONFLICT",
            message=payload.get("message") or "server_service returned 409",
            details=payload.get("details") or {},
        )
    if response.status_code >= 300:
        logger.warning(
            "server_service (internal) ответил %s на %s", response.status_code, path,
        )
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_ERROR",
            message=f"server_service returned {response.status_code}",
        )
    return _parse_json(response)


async def acquire_for_service(
    server_id: str,
    *,
    busy_state: str,
    busy_note: str | None = None,
    requested_by_department_id: str | None = None,
) -> dict:
    """POST /internal/servers/{id}/acquire-for-service — взять свободный стенд под цикл."""
    body: dict = {"busy_state": busy_state}
    if busy_note is not None:
        body["busy_note"] = busy_note
    if requested_by_department_id is not None:
        body["requested_by_department_id"] = requested_by_department_id
    return await _post_internal(f"{_INTERNAL_SERVERS_PATH}/{server_id}/acquire-for-service", body)


async def release_for_service(server_id: str) -> dict:
    """POST /internal/servers/{id}/release-for-service — отпустить стенд, очередь опустела."""
    return await _post_internal(f"{_INTERNAL_SERVERS_PATH}/{server_id}/release-for-service", {})


async def release_for_service_as_done(server_id: str) -> dict:
    """POST /internal/servers/{id}/release-for-service-as-done — очередь стенда

    опустела, но вместо `free` сервер паркуется в `testing_done`: кто-то
    должен явно подтвердить приёмку через человеческий
    `POST /servers/{id}/acknowledge-testing-done` на server_service.
    """
    return await _post_internal(
        f"{_INTERNAL_SERVERS_PATH}/{server_id}/release-for-service-as-done", {},
    )


async def set_service_status(
    server_id: str, *, busy_state: str, busy_note: str | None = None,
) -> dict:
    """POST /internal/servers/{id}/service-status — сменить стадию уже держащейся брони."""
    body: dict = {"busy_state": busy_state}
    if busy_note is not None:
        body["busy_note"] = busy_note
    return await _post_internal(f"{_INTERNAL_SERVERS_PATH}/{server_id}/service-status", body)


async def start_prepare_for_test(
    server_id: str,
    *,
    os_version_id: str,
    kernel: str,
    mode: str,
    test_username: str,
    requested_by_department_id: str | None,
    correlation_id: str,
) -> dict:
    """POST /internal/servers/{id}/prepare-for-test — запустить асинхронный пайплайн подготовки.

    Отвечает сразу `{prepare_request_id, status}` — реальный исход придёт
    callback'ом на `/internal/prepare-for-test/{prepare_request_id}/completed`
    (см. `api/v1/endpoints/internal_prepare_for_test.py`).
    """
    body = {
        "os_version_id": os_version_id,
        "kernel": kernel,
        "mode": mode,
        "test_username": test_username,
        "requested_by_department_id": requested_by_department_id,
        "correlation_id": correlation_id,
    }
    return await _post_internal(f"{_INTERNAL_SERVERS_PATH}/{server_id}/prepare-for-test", body)


async def get_servers_status_batch(server_ids: list[str]) -> dict[str, dict]:
    """POST /internal/servers/batch-status — ping/busy пачкой для обзора пула.

    Один вызов на весь пул стендов вместо N запросов на N стендов (§F плана
    2026-09-11 — «Обзор пула»). Отсутствующий/удалённый на стороне
    server_service сервер не роняет весь обзор: просто не попадает в
    результат, `services/pool_overview.py` трактует пропуск как «нет данных».
    """
    if not server_ids:
        return {}
    body = await _post_internal(f"{_INTERNAL_SERVERS_PATH}/batch-status", {"server_ids": server_ids})
    return {
        row["server_id"]: row
        for row in body.get("servers") or []
        if row.get("found")
    }


async def get_connection_info(server_id: str) -> dict:
    """GET /internal/servers/{id}/connection-info — IP стенда для SSH-исполнения теста.

    Отдельный узкий эндпоинт, не полная карточка сервера: `testing_worker`
    аутентифицируется shared-secret'ом, у него нет bearer'а пользователя для
    pass-through `get_server`, а держать IP в `test_stands` намеренно не
    стали (§4 плана — "без дублирования"). См. отчёт волны за обоснованием.
    """
    headers = _internal_headers()
    response = await _send_get(f"{_INTERNAL_SERVERS_PATH}/{server_id}/connection-info", None, headers)
    if response.status_code == 404:
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="server_service returned 404 for the requested server",
        )
    if response.status_code >= 300:
        logger.warning(
            "server_service (connection-info) ответил %s", response.status_code,
        )
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_ERROR",
            message=f"server_service returned {response.status_code}",
        )
    return _parse_json(response)


async def get_test_credentials(bearer_token: str, server_id: str, *, reveal: bool) -> dict:
    """GET /servers/{id}/test-credentials — прокси живой отладки (§5.3), pass-through bearer.

    Тот же гейт, что и у `get_server`: server_service проверяет
    `server_service.admin` через свою обычную матрицу `entity_permissions`,
    не shared-secret канал — значит нужен bearer вызывающего, не
    `SERVER_SERVICE_INTERNAL_API_KEY`.
    """
    path = f"{_SERVERS_PATH}/{server_id}/test-credentials"
    params = {"reveal": "true"} if reveal else None
    response = await _send_get(path, params, bearer_header(bearer_token))
    if response.status_code == 404:
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="Server not found or not visible to the caller",
        )
    if response.status_code == 403:
        raise AuthorizationError(
            error_code="SERVER_ACCESS_DENIED",
            message="server_service denied access to test credentials",
        )
    if response.status_code == 429:
        raise RateLimitError(
            error_code="RATE_LIMIT_EXCEEDED",
            message="server_service reveal rate-limit exceeded",
        )
    if response.status_code >= 300:
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_ERROR",
            message=f"server_service returned {response.status_code}",
        )
    return _parse_json(response)


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


async def resolve_os_kernels(os_version_id: str) -> list[str]:
    from urllib.parse import quote
    from src.core.exceptions import DomainValidationError
    body = await _get(f"{_OS_VERSIONS_PATH}/{quote(os_version_id, safe='')}/resolve-kernels", refresh=True)
    kernels = list(dict.fromkeys(body.get("kernels") or []))
    if not kernels:
        raise DomainValidationError(error_code="OS_KERNELS_NOT_FOUND", message="Для выбранной ОС не найдены ядра")
    return kernels
