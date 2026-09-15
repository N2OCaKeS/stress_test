"""Получение сервисных учётных данных (ACS, host-control SSH) без локального кэша значений."""
import base64
import binascii
from urllib.parse import quote, urlencode

import httpx

from src.core.config import get_settings
from src.core.exceptions import BadRequestError, ServiceUnavailableError
from src.core.http import bearer_header


def build_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


async def request(method: str, path: str, *, token: str | None = None, body: dict | None = None) -> httpx.Response:
    settings = get_settings()
    bearer = token or settings.secret_service_api_key
    if not settings.secret_service_url or not bearer:
        raise ServiceUnavailableError(error_code="SECRET_SERVICE_NOT_CONFIGURED", message="Не настроен доступ server_service к сервису секретов")
    try:
        async with build_client(settings.secret_request_timeout_seconds) as client:
            return await client.request(method, settings.secret_service_url.rstrip("/") + "/api/secret/v1" + path,
                headers={**bearer_header(bearer), "X-Service-Identity": "server_service"}, json=body)
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(error_code="SECRET_SERVICE_UNAVAILABLE", message="Сервис секретов недоступен; повторите операцию позже") from exc


def response_body(response: httpx.Response, *, unavailable_error_code: str = "ACS_CREDENTIAL_UNAVAILABLE") -> dict:
    if response.status_code in (401, 403, 404, 410):
        raise BadRequestError(error_code=unavailable_error_code, message="Учётные данные недоступны, заблокированы или срок их действия истёк")
    if not 200 <= response.status_code < 300:
        raise ServiceUnavailableError(error_code="SECRET_SERVICE_ERROR", message="Сервис секретов не выполнил запрос")
    try:
        body = response.json()
        if isinstance(body, dict):
            return body
    except ValueError:
        pass
    raise ServiceUnavailableError(error_code="SECRET_SERVICE_ERROR", message="Некорректный ответ сервиса секретов")


async def reveal_acs_password(credential_id: str) -> str:
    path = f"/credentials/{quote(credential_id, safe='')}"
    metadata = response_body(await request("GET", path))
    if metadata.get("scope") != "service" or str(metadata.get("service", "")).lower() != "acs" or not metadata.get("owner_dept_id"):
        raise BadRequestError(error_code="ACS_CREDENTIAL_INVALID", message="Выберите сервисные учётные данные системы acs с отделом-владельцем")
    body = response_body(await request("POST", path + "/reveal"))
    try:
        password = base64.b64decode(body["secret_b64"], validate=True).decode("utf-8")
        if password:
            return password
    except (KeyError, TypeError, ValueError, binascii.Error):
        pass
    raise ServiceUnavailableError(error_code="SECRET_SERVICE_ERROR", message="Сервис секретов вернул некорректное значение ACS")


async def list_acs_credentials() -> list[dict]:
    items = []
    cursor = None
    seen = set()
    while True:
        query = {"scope": "service", "service": "acs", "status": "active", "limit": 100}
        if cursor:
            query["cursor"] = cursor
        body = response_body(await request("GET", "/credentials?" + urlencode(query)))
        for item in body.get("items", []):
            if item.get("scope") == "service" and item.get("service") == "acs" and item.get("owner_dept_id"):
                items.append({key: item.get(key) for key in ("id", "name", "owner_dept_id", "valid_from", "valid_to")})
        cursor = body.get("next_cursor")
        if not cursor:
            return items
        if cursor in seen:
            raise ServiceUnavailableError(error_code="SECRET_SERVICE_ERROR", message="Некорректная пагинация сервиса секретов")
        seen.add(cursor)


async def reveal_host_ssh_key(credential_id: str, department_id: str) -> str:
    """Расшифрованный приватный SSH-ключ host-control записи `department_id`.

    В отличие от `reveal_acs_password` (платформенный singleton, владельца
    можно выбрать любого), эта запись привязана к конкретному отделу —
    `owner_dept_id` метаданных обязан совпасть с `department_id` caller'а,
    иначе один отдел мог бы направить свои настройки на чужой ключ.
    """
    path = f"/credentials/{quote(credential_id, safe='')}"
    metadata = response_body(await request("GET", path), unavailable_error_code="HOST_SSH_CREDENTIAL_UNAVAILABLE")
    if metadata.get("scope") != "service" or str(metadata.get("service", "")).lower() != "host_ssh" or metadata.get("owner_dept_id") != department_id:
        raise BadRequestError(error_code="HOST_SSH_CREDENTIAL_INVALID", message="Выберите сервисные учётные данные системы host_ssh, принадлежащие вашему отделу")
    body = response_body(await request("POST", path + "/reveal"), unavailable_error_code="HOST_SSH_CREDENTIAL_UNAVAILABLE")
    try:
        key = base64.b64decode(body["secret_b64"], validate=True).decode("utf-8")
        if key:
            return key
    except (KeyError, TypeError, ValueError, binascii.Error):
        pass
    raise ServiceUnavailableError(error_code="SECRET_SERVICE_ERROR", message="Сервис секретов вернул некорректное значение SSH-ключа")


async def list_host_ssh_credentials(department_id: str) -> list[dict]:
    """Сервисные записи `host_ssh`, принадлежащие `department_id` (для UI-выпадашки)."""
    items = []
    cursor = None
    seen = set()
    while True:
        query = {"scope": "service", "service": "host_ssh", "status": "active", "limit": 100}
        if cursor:
            query["cursor"] = cursor
        body = response_body(await request("GET", "/credentials?" + urlencode(query)), unavailable_error_code="HOST_SSH_CREDENTIAL_UNAVAILABLE")
        for item in body.get("items", []):
            if item.get("scope") == "service" and item.get("service") == "host_ssh" and item.get("owner_dept_id") == department_id:
                items.append({key: item.get(key) for key in ("id", "name", "owner_dept_id", "valid_from", "valid_to")})
        cursor = body.get("next_cursor")
        if not cursor:
            return items
        if cursor in seen:
            raise ServiceUnavailableError(error_code="SECRET_SERVICE_ERROR", message="Некорректная пагинация сервиса секретов")
        seen.add(cursor)
