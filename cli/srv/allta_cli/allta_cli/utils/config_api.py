from __future__ import annotations
from typing import Any, Optional, Union, Dict, TypedDict

import requests

from allta_cli.utils import ui
from allta_cli.utils.auth import load_token
from allta_cli.utils.config import CONFIG_API_BASE, SERVER_API_BASE
from allta_cli.utils.http_fallback import request_with_http_fallback


class ConfigApiError(RuntimeError):
    """Общая ошибка обращения к config-API."""


class TokenKeyNotFound(ConfigApiError):
    """Запрошенный ключ токена отсутствует в tokens.json."""


class ConfigApiFileNotFound(ConfigApiError):
    """Config-API вернул 404 для файла, содержит тело ответа сервера."""

    def __init__(self, filename: str, payload: Any):
        self.filename = filename
        self.payload = payload
        detail = None
        if isinstance(payload, dict):
            d = payload.get("detail")
            if isinstance(d, str) and d.strip():
                detail = d.strip()
        super().__init__(detail or f"File '{filename}' not found")


class IloEntry(TypedDict):
    ip: str
    username: str
    password: str


class ServiceCredential(TypedDict):
    id: int
    service_name: str
    username: str
    password: str
    updated_by: str | None
    created_at: str
    updated_at: str


class TokenCredential(TypedDict):
    id: int
    token_key: str
    token: str
    updated_by: str | None
    created_at: str
    updated_at: str


class ServiceCredentialNotFound(ConfigApiError):
    """Серверные креды для указанного сервиса не найдены."""

    def __init__(self, service_name: str):
        self.service_name = service_name
        super().__init__(f"Сервис '{service_name}' не найден.")


class TokenCredentialNotFound(ConfigApiError):
    """Токен с указанным ключом не найден."""

    def __init__(self, token_key: str):
        self.token_key = token_key
        super().__init__(f"Токен '{token_key}' не найден.")


def _auth_headers() -> Dict[str, str]:
    token = load_token(verbose=False)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
    return request_with_http_fallback(method, url, **kwargs)


def tokens(tokens_type: Optional[str] = None) -> Union[str, Dict[str, str]]:
    """
    Возвращает либо весь tokens.json, либо отдельный ключ.
    Использует единый стиль логирования и обработки ошибок.
    """
    url = f"{CONFIG_API_BASE}/config/tokens"
    ui.http(f"GET {url}")
    try:
        r = _request("GET", url, headers=_auth_headers(), timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить tokens.json: {e}") from e

    try:
        data = r.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при запросе /config/tokens.")

    if not isinstance(data, dict):
        raise ConfigApiError("Неверный формат ответа для tokens.json: ожидается объект JSON.")

    if tokens_type is None:
        ui.ok("tokens.json успешно получен.")
        return data

    if tokens_type in data and isinstance(data[tokens_type], str):
        ui.ok(f"Токен '{tokens_type}' успешно получен.")
        return data[tokens_type]

    raise TokenKeyNotFound(f"В tokens.json отсутствует ключ '{tokens_type}'.")


def ilo() -> Dict[str, IloEntry]:
    """
    Возвращает содержимое /server/ilo в формате:
    {
      "stand-name": {"ip": "...", "username": "...", "password": "..."}
    }
    """
    url = f"{SERVER_API_BASE}/ilo/"
    ui.http(f"GET {url}")
    try:
        r = _request("GET", url, headers=_auth_headers(), timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить iLO credentials: {e}") from e

    try:
        data = r.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при запросе /server/ilo.")

    if not isinstance(data, dict):
        raise ConfigApiError("Неверный формат ответа для /server/ilo: ожидается объект JSON.")

    result: Dict[str, IloEntry] = {}
    for stand, payload in data.items():
        if not isinstance(stand, str) or not stand.strip():
            raise ConfigApiError("Неверный формат /server/ilo: ключ стенда должен быть непустой строкой.")
        if not isinstance(payload, dict):
            raise ConfigApiError(f"Неверный формат /server/ilo: стенд '{stand}' должен содержать объект.")

        ip = payload.get("ip")
        username = payload.get("username")
        password = payload.get("password")
        if not all(isinstance(v, str) and v.strip() for v in (ip, username, password)):
            raise ConfigApiError(
                f"Неверный формат /server/ilo: у стенда '{stand}' ожидаются поля ip/username/password."
            )
        result[stand.strip()] = {
            "ip": ip.strip(),
            "username": username.strip(),
            "password": password.strip(),
        }

    ui.ok("iLO credentials успешно получены.")
    return result


def files(filename: str) -> Any:
    """
    Получает JSON-файл из config-API и возвращает его содержимое как dict.
    Все ошибки — в едином стиле через ConfigApiError.
    """
    if not filename:
        raise ConfigApiError("Не указано имя файла (filename).")

    url = f"{CONFIG_API_BASE}/config/files/{filename}"
    ui.http(f"GET {url}")

    try:
        r = _request("GET", url, headers=_auth_headers(), timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить файл '{filename}': {e}") from e

    if r.status_code == 404:
        try:
            payload = r.json()
        except ValueError:
            payload = {"detail": (r.text or "").strip() or f"File '{filename}' not found"}
        raise ConfigApiFileNotFound(filename=filename, payload=payload)

    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        try:
            payload = r.json()
            if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                raise ConfigApiError(f"HTTP {r.status_code}: {payload['detail']}")
        except ValueError:
            pass
        raise ConfigApiError(f"Не удалось получить файл '{filename}': {e}") from e

    try:
        js = r.json()
    except ValueError:
        snippet = (r.text or "")[:200]
        raise ConfigApiError(
            f"Файл '{filename}' не является корректным JSON. "
            f"Фрагмент ответа: {snippet!r}"
        )

    ui.ok(f"Файл '{filename}' успешно получен.")
    return js


def _response_detail(resp: requests.Response) -> str | None:
    try:
        payload = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return text or None

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return None


def _normalize_token_credential(item: Any, *, context: str) -> TokenCredential:
    if not isinstance(item, dict):
        raise ConfigApiError(f"Неверный формат ответа для {context}: ожидается объект JSON.")

    id_value = item.get("id")
    token_key = item.get("token_key")
    token_value = item.get("token")
    updated_by = item.get("updated_by")
    created_at = item.get("created_at")
    updated_at = item.get("updated_at")

    if not isinstance(id_value, int):
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'id' должно быть числом.")
    if not isinstance(token_key, str) or not token_key.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'token_key' должно быть строкой.")
    if not isinstance(token_value, str) or not token_value.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'token' должно быть строкой.")
    if updated_by is not None and not isinstance(updated_by, str):
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'updated_by' должно быть строкой или null.")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'created_at' должно быть строкой.")
    if not isinstance(updated_at, str) or not updated_at.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'updated_at' должно быть строкой.")

    return {
        "id": id_value,
        "token_key": token_key.strip(),
        "token": token_value.strip(),
        "updated_by": updated_by.strip() if isinstance(updated_by, str) else None,
        "created_at": created_at.strip(),
        "updated_at": updated_at.strip(),
    }


def list_token_credentials() -> list[TokenCredential]:
    url = f"{CONFIG_API_BASE}/config/tokens/details"
    ui.http(f"GET {url}")
    try:
        resp = _request("GET", url, headers=_auth_headers(), timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить список токенов: {e}") from e

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось получить список токенов: {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при запросе /config/tokens/details.")

    if not isinstance(data, list):
        raise ConfigApiError("Неверный формат ответа для /config/tokens/details: ожидается список JSON.")

    result = [_normalize_token_credential(item, context="/config/tokens/details") for item in data]
    ui.ok("Токены успешно получены.")
    return result


def get_token_credential(token_key: str) -> TokenCredential:
    key = token_key.strip()
    if not key:
        raise ConfigApiError("Не указан ключ токена (token_key).")

    url = f"{CONFIG_API_BASE}/config/tokens/details/{key}"
    ui.http(f"GET {url}")
    try:
        resp = _request("GET", url, headers=_auth_headers(), timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить токен '{key}': {e}") from e

    if resp.status_code == 404:
        raise TokenCredentialNotFound(key)

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось получить токен '{key}': {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise ConfigApiError(f"Сервер вернул не-JSON при запросе /config/tokens/details/{key}.")

    ui.ok(f"Токен '{key}' успешно получен.")
    return _normalize_token_credential(data, context=f"/config/tokens/details/{key}")


def upsert_token_credential(token_key: str, token: str) -> TokenCredential:
    key = token_key.strip()
    value = token.strip()
    if not key:
        raise ConfigApiError("Не указан ключ токена (token_key).")
    if not value:
        raise ConfigApiError("Не указан токен (token).")

    url = f"{CONFIG_API_BASE}/config/tokens/details"
    payload = {
        "token_key": key,
        "token": value,
    }
    ui.http(f"POST {url}")
    try:
        resp = _request("POST", url, headers=_auth_headers(), json=payload, timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось сохранить токен '{key}': {e}") from e

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось сохранить токен '{key}': {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при сохранении токена.")

    ui.ok(f"Токен '{key}' сохранён.")
    return _normalize_token_credential(data, context="/config/tokens/details")


def update_token_credential(token_key: str, token: str) -> TokenCredential:
    key = token_key.strip()
    value = token.strip()
    if not key:
        raise ConfigApiError("Не указан ключ токена (token_key).")
    if not value:
        raise ConfigApiError("Не указан токен (token).")

    url = f"{CONFIG_API_BASE}/config/tokens/details/{key}"
    ui.http(f"PATCH {url}")
    try:
        resp = _request("PATCH", url, headers=_auth_headers(), json={"token": value}, timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось обновить токен '{key}': {e}") from e

    if resp.status_code == 404:
        raise TokenCredentialNotFound(key)

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось обновить токен '{key}': {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при обновлении токена.")

    ui.ok(f"Токен '{key}' обновлён.")
    return _normalize_token_credential(data, context=f"/config/tokens/details/{key}")


def delete_token_credential(token_key: str) -> None:
    key = token_key.strip()
    if not key:
        raise ConfigApiError("Не указан ключ токена (token_key).")

    url = f"{CONFIG_API_BASE}/config/tokens/details/{key}"
    ui.http(f"DELETE {url}")
    try:
        resp = _request("DELETE", url, headers=_auth_headers(), timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось удалить токен '{key}': {e}") from e

    if resp.status_code == 404:
        raise TokenCredentialNotFound(key)

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось удалить токен '{key}': {e}") from e

    ui.ok(f"Токен '{key}' удалён.")


def _normalize_service_credential(item: Any, *, context: str) -> ServiceCredential:
    if not isinstance(item, dict):
        raise ConfigApiError(f"Неверный формат ответа для {context}: ожидается объект JSON.")

    id_value = item.get("id")
    service_name = item.get("service_name")
    username = item.get("username")
    password = item.get("password")
    updated_by = item.get("updated_by")
    created_at = item.get("created_at")
    updated_at = item.get("updated_at")

    if not isinstance(id_value, int):
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'id' должно быть числом.")
    if not isinstance(service_name, str) or not service_name.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'service_name' должно быть строкой.")
    if not isinstance(username, str) or not username.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'username' должно быть строкой.")
    if not isinstance(password, str) or not password.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'password' должно быть строкой.")
    if updated_by is not None and not isinstance(updated_by, str):
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'updated_by' должно быть строкой или null.")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'created_at' должно быть строкой.")
    if not isinstance(updated_at, str) or not updated_at.strip():
        raise ConfigApiError(f"Неверный формат ответа для {context}: поле 'updated_at' должно быть строкой.")

    return {
        "id": id_value,
        "service_name": service_name.strip(),
        "username": username.strip(),
        "password": password.strip(),
        "updated_by": updated_by.strip() if isinstance(updated_by, str) else None,
        "created_at": created_at.strip(),
        "updated_at": updated_at.strip(),
    }


def list_service_credentials() -> list[ServiceCredential]:
    url = f"{CONFIG_API_BASE}/config/credentials"
    ui.http(f"GET {url}")
    try:
        resp = _request("GET", url, headers=_auth_headers(), timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить список сервисных кредов: {e}") from e

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось получить список сервисных кредов: {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при запросе /config/credentials.")

    if not isinstance(data, list):
        raise ConfigApiError("Неверный формат ответа для /config/credentials: ожидается список JSON.")

    result = [_normalize_service_credential(item, context="/config/credentials") for item in data]
    ui.ok("Сервисные креды успешно получены.")
    return result


def get_service_credential(service_name: str) -> ServiceCredential:
    name = service_name.strip()
    if not name:
        raise ConfigApiError("Не указано имя сервиса (service_name).")

    url = f"{CONFIG_API_BASE}/config/credentials/{name}"
    ui.http(f"GET {url}")
    try:
        resp = _request("GET", url, headers=_auth_headers(), timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить креды сервиса '{name}': {e}") from e

    if resp.status_code == 404:
        raise ServiceCredentialNotFound(name)

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось получить креды сервиса '{name}': {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise ConfigApiError(f"Сервер вернул не-JSON при запросе /config/credentials/{name}.")

    ui.ok(f"Креды сервиса '{name}' успешно получены.")
    return _normalize_service_credential(data, context=f"/config/credentials/{name}")


def upsert_service_credential(service_name: str, username: str, password: str) -> ServiceCredential:
    name = service_name.strip()
    user = username.strip()
    pwd = password.strip()
    if not name:
        raise ConfigApiError("Не указано имя сервиса (service_name).")
    if not user or not pwd:
        raise ConfigApiError("Для сохранения нужны и логин, и пароль.")

    url = f"{CONFIG_API_BASE}/config/credentials"
    payload = {
        "service_name": name,
        "username": user,
        "password": pwd,
    }
    ui.http(f"POST {url}")
    try:
        resp = _request("POST", url, headers=_auth_headers(), json=payload, timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось сохранить креды сервиса '{name}': {e}") from e

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось сохранить креды сервиса '{name}': {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при сохранении сервисных кредов.")

    ui.ok(f"Креды сервиса '{name}' сохранены.")
    return _normalize_service_credential(data, context="/config/credentials")


def update_service_credential(
    service_name: str,
    *,
    username: str | None = None,
    password: str | None = None,
) -> ServiceCredential:
    name = service_name.strip()
    if not name:
        raise ConfigApiError("Не указано имя сервиса (service_name).")

    payload: Dict[str, str] = {}
    if username is not None:
        payload["username"] = username.strip()
    if password is not None:
        payload["password"] = password.strip()
    if not payload:
        raise ConfigApiError("Для обновления укажите хотя бы одно поле: username или password.")

    url = f"{CONFIG_API_BASE}/config/credentials/{name}"
    ui.http(f"PATCH {url}")
    try:
        resp = _request("PATCH", url, headers=_auth_headers(), json=payload, timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось обновить креды сервиса '{name}': {e}") from e

    if resp.status_code == 404:
        raise ServiceCredentialNotFound(name)

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось обновить креды сервиса '{name}': {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при обновлении сервисных кредов.")

    ui.ok(f"Креды сервиса '{name}' обновлены.")
    return _normalize_service_credential(data, context=f"/config/credentials/{name}")


def delete_service_credential(service_name: str) -> None:
    name = service_name.strip()
    if not name:
        raise ConfigApiError("Не указано имя сервиса (service_name).")

    url = f"{CONFIG_API_BASE}/config/credentials/{name}"
    ui.http(f"DELETE {url}")
    try:
        resp = _request("DELETE", url, headers=_auth_headers(), timeout=30)
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось удалить креды сервиса '{name}': {e}") from e

    if resp.status_code == 404:
        raise ServiceCredentialNotFound(name)

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _response_detail(resp)
        if detail:
            raise ConfigApiError(f"HTTP {resp.status_code}: {detail}") from e
        raise ConfigApiError(f"Не удалось удалить креды сервиса '{name}': {e}") from e

    ui.ok(f"Креды сервиса '{name}' удалены.")
