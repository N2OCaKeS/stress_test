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

стенд — сервер или ВМ. Функции канала брони/подготовки
принимают `target: StandTarget` (`acquire_stand`, `release_stand`,
`release_stand_as_done`, `set_stand_service_status`,
`start_stand_prepare_for_test`, `start_stand_setup_for`, `get_stand_connection_info`,
`get_stands_status_batch`) и сами выбирают путь `/internal/servers/{id}/…` или
`/internal/vms/{id}/…`; прежние функции по `server_id` остались обёртками.

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
import time
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
from src.services.stand_target import StandTarget

logger = logging.getLogger("testing_service.server_client")

_OS_VERSIONS_PATH = "/api/server/v1/os-versions"
_SERVERS_PATH = "/api/server/v1/servers"
_INTERNAL_SERVERS_PATH = "/api/server/v1/internal/servers"
_INTERNAL_VMS_PATH = "/api/server/v1/internal/vms"
_VMS_PATH = "/api/server/v1/vms"

# «Бронь не держится» — сервер и ВМ отвечают своим кодом.
NOT_BUSY_ERROR_CODES = ("SERVER_NOT_BUSY", "VM_NOT_BUSY")


def _target_path(target: StandTarget) -> str:
    base = _INTERNAL_VMS_PATH if target.is_vm else _INTERNAL_SERVERS_PATH
    return f"{base}/{target.id}"


def _not_found_code(path: str) -> str:
    return "VM_NOT_FOUND" if "/internal/vms/" in path or path.startswith(_VMS_PATH) else "SERVER_NOT_FOUND"

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
            error_code=_not_found_code(path),
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
            error_code=_not_found_code(path),
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


async def _acquire(
    target: StandTarget,
    *,
    busy_state: str,
    busy_note: str | None = None,
    requested_by_department_id: str | None = None,
    takeover: bool = False,
) -> dict:
    """POST /internal/{servers|vms}/{id}/acquire-for-service — взять стенд под цикл.

    `takeover=True` переписывает бронь занятого (`busy`/`testing_done`) стенда
    на testing_service; в ответе приходит `previous_holder`. Поле уходит в тело
    только когда оно включено — обычный захват остаётся прежним запросом.
    """
    body: dict = {"busy_state": busy_state}
    if takeover:
        body["takeover"] = True
    if busy_note is not None:
        body["busy_note"] = busy_note
    if requested_by_department_id is not None:
        body["requested_by_department_id"] = requested_by_department_id
    return await _post_internal(f"{_target_path(target)}/acquire-for-service", body)


async def acquire_for_service(
    server_id: str,
    *,
    busy_state: str,
    busy_note: str | None = None,
    requested_by_department_id: str | None = None,
    takeover: bool = False,
) -> dict:
    """POST /internal/servers/{id}/acquire-for-service (см. `acquire_stand`)."""
    return await _acquire(
        StandTarget.server(server_id), busy_state=busy_state, busy_note=busy_note,
        requested_by_department_id=requested_by_department_id, takeover=takeover,
    )


async def _release(target: StandTarget) -> dict:
    """POST …/release-for-service — отпустить стенд."""
    return await _post_internal(f"{_target_path(target)}/release-for-service", {})


async def release_for_service(server_id: str) -> dict:
    """POST /internal/servers/{id}/release-for-service — отпустить стенд, очередь опустела."""
    return await _release(StandTarget.server(server_id))


async def _release_as_done(target: StandTarget) -> dict:
    """POST …/release-for-service-as-done — очередь стенда опустела, но вместо
    `free` стенд паркуется в `testing_done`: кто-то должен явно подтвердить
    приёмку (сервер — `POST /servers/{id}/acknowledge-testing-done`, ВМ —
    `POST /vms/{id}/release` на server_service).
    """
    return await _post_internal(f"{_target_path(target)}/release-for-service-as-done", {})


async def release_for_service_as_done(server_id: str) -> dict:
    """POST /internal/servers/{id}/release-for-service-as-done (см. `release_stand_as_done`)."""
    return await _release_as_done(StandTarget.server(server_id))


async def _set_status(
    target: StandTarget, *, busy_state: str, busy_note: str | None = None,
) -> dict:
    """POST …/service-status — сменить стадию уже держащейся брони."""
    body: dict = {"busy_state": busy_state}
    if busy_note is not None:
        body["busy_note"] = busy_note
    return await _post_internal(f"{_target_path(target)}/service-status", body)


async def set_service_status(
    server_id: str, *, busy_state: str, busy_note: str | None = None,
) -> dict:
    """POST /internal/servers/{id}/service-status (см. `set_stand_service_status`)."""
    return await _set_status(StandTarget.server(server_id), busy_state=busy_state, busy_note=busy_note)


async def _start_prepare(
    target: StandTarget,
    *,
    os_version_id: str,
    kernel: str,
    mode: str,
    test_username: str,
    requested_by_department_id: str | None,
    correlation_id: str,
    test_account_credential_id: str | None = None,
    stand_setup: dict | None = None,
    provisioning: dict | None = None,
    preparation: str = "full",
    skip_pam_fix: bool = False,
) -> dict:
    """POST …/prepare-for-test — запустить асинхронный пайплайн подготовки.

    Отвечает сразу `{prepare_request_id, status}` — реальный исход придёт
    callback'ом на `/internal/prepare-for-test/{prepare_request_id}/completed`
    (см. `api/v1/endpoints/internal_prepare_for_test.py`).

    `test_account_credential_id` — ссылка на тестовую
    учётку отдела в secret_service: server_service ставит на стенд её логин,
    пароль и публичный ключ вместо случайных. Без неё поле не шлётся вовсе.

    ВМ: путь `/internal/vms/{id}/prepare-for-test` и `target` в теле
    (C2); вместо ACS restore server_service откатывает снимок ВМ.

    `preparation="revert_only"` (стенд сценария) — без смены режима и без
    шага настройки; `full` в теле не шлётся, это умолчание server_service.
    `skip_pam_fix` — без PAM-правки; шлётся только `true`.
    """
    body = {
        "os_version_id": os_version_id,
        "kernel": kernel,
        "mode": mode,
        "test_username": test_username,
        "requested_by_department_id": requested_by_department_id,
        "correlation_id": correlation_id,
    }
    if test_account_credential_id:
        body["test_account_credential_id"] = test_account_credential_id
    if stand_setup:
        # шаг настройки стенда теста, скрипт уже отрезолвлен.
        body["stand_setup"] = stand_setup
    if provisioning:
        # значения профиля подготовки (server_service профилей не знает).
        body["provisioning"] = provisioning
    if preparation != "full":
        body["preparation"] = preparation
    if skip_pam_fix:
        body["skip_pam_fix"] = True
    if target.is_vm:
        body["target"] = {"type": "vm", "vm_id": target.id}
    return await _post_internal(f"{_target_path(target)}/prepare-for-test", body)


async def start_prepare_for_test(
    server_id: str,
    *,
    os_version_id: str,
    kernel: str,
    mode: str,
    test_username: str,
    requested_by_department_id: str | None,
    correlation_id: str,
    test_account_credential_id: str | None = None,
    stand_setup: dict | None = None,
    provisioning: dict | None = None,
    preparation: str = "full",
    skip_pam_fix: bool = False,
) -> dict:
    """POST /internal/servers/{id}/prepare-for-test (см. `start_stand_prepare_for_test`)."""
    return await _start_prepare(
        StandTarget.server(server_id), os_version_id=os_version_id, kernel=kernel, mode=mode,
        test_username=test_username, requested_by_department_id=requested_by_department_id,
        correlation_id=correlation_id, test_account_credential_id=test_account_credential_id,
        stand_setup=stand_setup, provisioning=provisioning, preparation=preparation,
        skip_pam_fix=skip_pam_fix,
    )


async def _stand_setup(
    target: StandTarget,
    *,
    correlation_id: str,
    requested_by_department_id: str | None,
    test_username: str,
    stand_setup: dict,
    provisioning: dict | None = None,
) -> dict:
    """POST …/stand-setup — настройка без restore (у ВМ — без отката снимка).

    Отвечает `{stand_setup_request_id, status}`; исход — callback на
    `/internal/stand-setup/{id}/completed`. Вызывает очередь перед шагом
    многоступенчатого теста.
    """
    body = {
        "correlation_id": correlation_id,
        "requested_by_department_id": requested_by_department_id,
        "test_username": test_username,
        "stand_setup": stand_setup,
    }
    if provisioning:
        body["provisioning"] = provisioning
    return await _post_internal(f"{_target_path(target)}/stand-setup", body)


async def start_stand_setup(server_id: str, **kwargs) -> dict:
    """POST /internal/servers/{id}/stand-setup (см. `start_stand_setup_for`)."""
    return await _stand_setup(StandTarget.server(server_id), **kwargs)


async def _status_batch(targets: list[StandTarget]) -> dict[StandTarget, dict]:
    """POST /internal/servers/batch-status — ping/busy пачкой для обзора пула.

    Один вызов на весь пул стендов вместо N запросов на N стендов (§F плана
    2026-09-11 — «Обзор пула»); список смешанный: `server_ids` и
    `vm_ids`, у ВМ бронь уже сведена server_service'ом к `busy_state`
    серверов. Отсутствующий на стороне server_service стенд не роняет весь
    обзор: просто не попадает в результат.
    """
    server_ids = [t.id for t in targets if not t.is_vm]
    vm_ids = [t.id for t in targets if t.is_vm]
    if not server_ids and not vm_ids:
        return {}
    request: dict = {"server_ids": server_ids}
    if vm_ids:
        request["vm_ids"] = vm_ids
    body = await _post_internal(f"{_INTERNAL_SERVERS_PATH}/batch-status", request)
    result: dict[StandTarget, dict] = {
        StandTarget.server(row["server_id"]): row
        for row in body.get("servers") or []
        if row.get("found")
    }
    for row in body.get("vms") or []:
        if row.get("found"):
            result[StandTarget.vm(row["vm_id"])] = row
    return result


async def get_servers_status_batch(server_ids: list[str]) -> dict[str, dict]:
    """Batch-status только по серверам (ключ — `server_id`), см. `get_stands_status_batch`."""
    statuses = await _status_batch([StandTarget.server(sid) for sid in server_ids])
    return {target.id: row for target, row in statuses.items()}


async def _connection_info(target: StandTarget) -> dict:
    """GET …/connection-info — IP стенда (сервера или гостя ВМ) для SSH-исполнения теста.

    Отдельный узкий эндпоинт, не полная карточка сервера: `testing_worker`
    аутентифицируется shared-secret'ом, у него нет bearer'а пользователя для
    pass-through `get_server`, а держать IP в `test_stands` намеренно не
    стали (§4 плана — "без дублирования"). См. отчёт волны за обоснованием.
    """
    headers = _internal_headers()
    path = f"{_target_path(target)}/connection-info"
    response = await _send_get(path, None, headers)
    if response.status_code == 404:
        raise NotFoundError(
            error_code=_not_found_code(path),
            message="server_service returned 404 for the requested server",
        )
    if response.status_code >= 300:
        payload = _parse_json_or_empty(response)
        logger.warning(
            "server_service (connection-info) ответил %s", response.status_code,
        )
        raise ServiceUnavailableError(
            error_code=payload.get("error_code") or "SERVER_SERVICE_ERROR",
            message=payload.get("message") or f"server_service returned {response.status_code}",
        )
    return _parse_json(response)


async def get_connection_info(server_id: str) -> dict:
    """GET /internal/servers/{id}/connection-info (см. `get_stand_connection_info`)."""
    return await _connection_info(StandTarget.server(server_id))


# ── Публичный API по цели стенда ───────────────────────
#
# Для `server` — через именованные функции по `server_id` выше (их подменяют
# тесты и параллельные задачи, поведение одно), для `vm` — тот же код по пути
# `/internal/vms/{id}/…`.


async def acquire_stand(
    target: StandTarget,
    *,
    busy_state: str,
    busy_note: str | None = None,
    requested_by_department_id: str | None = None,
    takeover: bool = False,
) -> dict:
    """Взять стенд (сервер или ВМ) под цикл — `…/acquire-for-service`."""
    kwargs = {
        "busy_state": busy_state, "busy_note": busy_note,
        "requested_by_department_id": requested_by_department_id, "takeover": takeover,
    }
    if target.is_vm:
        return await _acquire(target, **kwargs)
    return await acquire_for_service(target.id, **kwargs)


async def release_stand(target: StandTarget) -> dict:
    """Отпустить стенд — `…/release-for-service`."""
    return await (_release(target) if target.is_vm else release_for_service(target.id))


async def release_stand_as_done(target: StandTarget) -> dict:
    """Очередь опустела — `…/release-for-service-as-done` (стенд в `testing_done`)."""
    return await (_release_as_done(target) if target.is_vm else release_for_service_as_done(target.id))


async def set_stand_service_status(
    target: StandTarget, *, busy_state: str, busy_note: str | None = None,
) -> dict:
    """Сменить стадию держащейся брони — `…/service-status`."""
    if target.is_vm:
        return await _set_status(target, busy_state=busy_state, busy_note=busy_note)
    return await set_service_status(target.id, busy_state=busy_state, busy_note=busy_note)


async def start_stand_prepare_for_test(target: StandTarget, **kwargs) -> dict:
    """Запустить `prepare-for-test` стенда; аргументы — как у `start_prepare_for_test`."""
    if target.is_vm:
        return await _start_prepare(target, **kwargs)
    return await start_prepare_for_test(target.id, **kwargs)


async def start_stand_setup_for(target: StandTarget, **kwargs) -> dict:
    """Настройка стенда без restore; аргументы — как у `start_stand_setup`."""
    if target.is_vm:
        return await _stand_setup(target, **kwargs)
    return await start_stand_setup(target.id, **kwargs)


async def get_stands_status_batch(targets: list[StandTarget]) -> dict[StandTarget, dict]:
    """Ping/busy пула одним вызовом; ключ — `StandTarget`. Только серверы — `get_servers_status_batch`."""
    if any(target.is_vm for target in targets):
        return await _status_batch(targets)
    statuses = await get_servers_status_batch([target.id for target in targets]) if targets else {}
    return {StandTarget.server(server_id): row for server_id, row in statuses.items()}


async def get_stand_connection_info(target: StandTarget) -> dict:
    """IP стенда (сервера или гостя ВМ) — `…/connection-info`."""
    return await (_connection_info(target) if target.is_vm else get_connection_info(target.id))


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


async def get_vm(bearer_token: str, vm_id: str) -> dict:
    """Карточка ВМ (`GET /vms/{id}`), pass-through bearer'а — как `get_server`."""
    return await _get_passthrough(f"{_VMS_PATH}/{vm_id}", bearer_token)


async def get_stand_card(bearer_token: str, target: StandTarget) -> dict:
    """Живая карточка стенда: сервера или ВМ."""
    if target.is_vm:
        return await get_vm(bearer_token, target.id)
    return await get_server(bearer_token, target.id)


async def resolve_os_kernels(os_version_id: str) -> list[str]:
    from urllib.parse import quote
    from src.core.exceptions import DomainValidationError
    body = await _get(f"{_OS_VERSIONS_PATH}/{quote(os_version_id, safe='')}/resolve-kernels", refresh=True)
    kernels = list(dict.fromkeys(body.get("kernels") or []))
    if not kernels:
        raise DomainValidationError(error_code="OS_KERNELS_NOT_FOUND", message="Для выбранной ОС не найдены ядра")
    return kernels


@dataclass(frozen=True)
class AcsSnapshotVersions:
    """Версии РЦ, для которых у ACS есть снимок стенда.

    `versions` — хвосты имён снимков после `{hostname}-` как есть **и** в
    форме каталога (`normalized_version` от server_service: `1710rc52` →
    `1.7.10.52`). Имя версии каталога (`os_version.name`) проверяется
    `in` — совпадение с любой из форм. Нормализацию считает server_service
    (`core/known_os.py::normalize_os_version_name`), здесь её копии нет.
    `hostname` — префикс имён снимков стенда, для текста ошибки.
    """

    hostname: str | None
    versions: frozenset[str]

    def __contains__(self, version_name: object) -> bool:
        return version_name in self.versions

    def snapshot_name(self, version_name: str) -> str:
        """Искомое имя снимка `{hostname}-{version}` (без hostname — только версия)."""
        return f"{self.hostname}-{version_name}" if self.hostname else version_name


# server_id → (monotonic-дедлайн, версии РЦ со снимком). Постановка в очередь
# кампании штампует десятки item'ов подряд на один и тот же стенд — без кэша
# каждый из них сходил бы в ACS по сети синхронно. TTL короткий: это гейт
# перед восстановлением стенда, не витрина, устаревать он не должен надолго.
_ACS_SNAPSHOT_CACHE_TTL_SECONDS = 20.0
_acs_snapshot_cache: dict[str, tuple[float, AcsSnapshotVersions]] = {}


async def list_acs_snapshot_versions(
    server_id: str, *, refresh: bool = False,
) -> AcsSnapshotVersions:
    """Версии РЦ, для которых у ACS есть снимок диска этого стенда.

    `GET /internal/servers/{id}/acs-snapshots`, за коротким in-memory кэшем
    (см. `_ACS_SNAPSHOT_CACHE_TTL_SECONDS`) — используется
    `services/queue.py::enqueue()` перед постановкой в очередь: без снимка
    `prepare-for-test` не сможет откатить стенд на выбранную РЦ, и отказ иначе
    пришёл бы часы спустя асинхронно из `acs.snapshot_restore`. Ошибки ACS
    (disabled/timeout/unreachable/ошибочный статус) со стороны server_service
    пробрасываются как есть — `error_code` из тела ответа, не кэшируются.

    Версии — и хвосты имён как есть, и `normalized_version`: снимки
    на ACS бывают названы компактно (`LowServer-1710rc52`), а каталог хранит
    `1.7.10.52`. Старый server_service без `normalized_version` — только хвосты.
    """
    now = time.monotonic()
    if not refresh:
        cached = _acs_snapshot_cache.get(server_id)
        if cached is not None and cached[0] > now:
            return cached[1]

    headers = _internal_headers()
    response = await _send_get(f"{_INTERNAL_SERVERS_PATH}/{server_id}/acs-snapshots", None, headers)
    if response.status_code == 404:
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND",
            message="server_service returned 404 for the requested server",
        )
    if response.status_code >= 300:
        payload = _parse_json_or_empty(response)
        logger.warning(
            "server_service (acs-snapshots) ответил %s на server_id=%s", response.status_code, server_id,
        )
        raise ServiceUnavailableError(
            error_code=payload.get("error_code") or "SERVER_SERVICE_ERROR",
            message=payload.get("message") or f"server_service returned {response.status_code}",
            details=payload.get("details") or {},
        )
    body = _parse_json(response)
    versions: set[str] = set()
    for item in body.get("snapshots") or []:
        for key in ("version_name", "normalized_version"):
            if item.get(key):
                versions.add(str(item[key]))
    result = AcsSnapshotVersions(hostname=body.get("hostname"), versions=frozenset(versions))
    _acs_snapshot_cache[server_id] = (now + _ACS_SNAPSHOT_CACHE_TTL_SECONDS, result)
    return result


# vm_id → (monotonic-дедлайн, тело ответа). Тот же приём и TTL, что у
# `_acs_snapshot_cache`: кампания штампует десятки item'ов на одну ВМ подряд.
_vm_snapshot_cache: dict[str, tuple[float, dict]] = {}


async def list_vm_test_snapshots(vm_id: str, *, refresh: bool = False) -> dict:
    """Снимки ВМ для отката перед тестом — `GET /internal/vms/{id}/snapshots`.

    Тело — как отдаёт server_service: `snapshots` (имя, `version_name`,
    `normalized_version` — версия из имени по шаблонам, после нормализации;
    `mode`) и `templates`. Ошибки — как у `list_acs_snapshot_versions`.
    """
    now = time.monotonic()
    if not refresh:
        cached = _vm_snapshot_cache.get(vm_id)
        if cached is not None and cached[0] > now:
            return cached[1]
    headers = _internal_headers()
    path = f"{_INTERNAL_VMS_PATH}/{vm_id}/snapshots"
    response = await _send_get(path, None, headers)
    if response.status_code == 404:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="server_service returned 404 for the requested VM")
    if response.status_code >= 300:
        payload = _parse_json_or_empty(response)
        logger.warning("server_service (vm snapshots) ответил %s на vm_id=%s", response.status_code, vm_id)
        raise ServiceUnavailableError(
            error_code=payload.get("error_code") or "SERVER_SERVICE_ERROR",
            message=payload.get("message") or f"server_service returned {response.status_code}",
            details=payload.get("details") or {},
        )
    body = _parse_json(response)
    _vm_snapshot_cache[vm_id] = (now + _ACS_SNAPSHOT_CACHE_TTL_SECONDS, body)
    return body
