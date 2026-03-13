from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, List, TypedDict

import requests

from allta_cli.utils import auth
from allta_cli.utils.config import SERVER_API_BASE


class ServerError(RuntimeError):
    pass


class SnapshotPasswordEntry(TypedDict):
    id: int
    os_version_name: str
    ssh_username: str
    password: str
    updated_by: str | None
    created_at: str
    updated_at: str


class OSVersionsSyncEntry(TypedDict):
    source_url: str
    added: int
    total: int


class OSVersionEntry(TypedDict):
    id: int
    name: str


class SnapshotPasswordNotFound(ServerError):
    def __init__(self, os_version_name: str):
        super().__init__(f"Пароль для версии ОС '{os_version_name}' не найден.")
        self.os_version_name = os_version_name


def _headers() -> Dict[str, str]:
    token = auth.load_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _api_url(path: str) -> str:
    return f"{SERVER_API_BASE}{path}"


def _request(method: str, path: str, *, params=None, json=None, timeout=30) -> requests.Response:
    url = _api_url(path)
    resp = requests.request(
        method=method,
        url=url,
        headers=_headers(),
        params=params,
        json=json,
        timeout=timeout,
        allow_redirects=False,
    )
    if 300 <= resp.status_code < 400:
        loc = resp.headers.get("location")
        raise ServerError(f"Redirect {resp.status_code} to {loc or '?'} for {url}")
    if resp.status_code >= 400:
        _raise_server_error(resp)
    return resp


def _parse_json(resp: requests.Response, *, context: str) -> Any:
    try:
        return resp.json()
    except ValueError as e:
        raise ServerError(f"Некорректный ответ сервера для {context}: {resp.text}") from e


def _stand_number(name: str) -> int | None:
    match = re.match(r"^\s*stand(?P<stand>\d+)_", name, flags=re.IGNORECASE)
    if match:
        return int(match.group("stand"))

    # Backward compatibility: legacy format like "12-name" or "stand12-name".
    match = re.match(r"^\s*(?:stand)?(?P<stand>\d+)-", name, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group("stand"))


def stand_number_text(name: str) -> str:
    number = _stand_number(name)
    return str(number) if number is not None else "-"


def server_sort_key(server: dict) -> tuple[int, str]:
    name = str(server.get("name") or "").strip()
    number = _stand_number(name)
    return (number if number is not None else 10**9, name.lower())


def _normalize_os_version(item: Any, *, context: str) -> OSVersionEntry:
    if not isinstance(item, dict):
        raise ServerError(f"Некорректный ответ сервера для {context}: ожидается объект.")

    id_value = item.get("id")
    name = item.get("name")
    if not isinstance(id_value, int):
        raise ServerError(f"Некорректный ответ сервера для {context}: поле 'id' должно быть числом.")
    if not isinstance(name, str) or not name.strip():
        raise ServerError(f"Некорректный ответ сервера для {context}: поле 'name' должно быть строкой.")
    return {"id": id_value, "name": name.strip()}


def _normalize_server_payload(server: dict) -> dict:
    result = dict(server)
    os_version_name = str(result.get("os_version") or result.get("os_version_name") or "").strip()
    if os_version_name:
        result["os_version"] = os_version_name
    return result


def list_os_versions(*, skip: int = 0, limit: int = 1000) -> list[OSVersionEntry]:
    resp = _request("GET", "/os-versions/", params={"skip": skip, "limit": limit}, timeout=20)
    data = _parse_json(resp, context="список версий ОС")
    if not isinstance(data, list):
        raise ServerError("Некорректный ответ сервера: ожидается список версий ОС.")
    return [_normalize_os_version(item, context="/os-versions") for item in data]


def _enrich_servers_with_snapshot_credentials(servers: list[dict]) -> list[dict]:
    if not servers:
        return []

    result = [_normalize_server_payload(server) for server in servers if isinstance(server, dict)]
    if not result:
        return []

    os_name_by_id: dict[int, str] = {}
    missing_os_ids: set[int] = set()
    for server in result:
        os_id = server.get("os_version_id")
        os_name = str(server.get("os_version") or server.get("os_version_name") or "").strip()
        if os_name and isinstance(os_id, int):
            os_name_by_id[os_id] = os_name
        elif isinstance(os_id, int):
            missing_os_ids.add(os_id)

    if missing_os_ids:
        try:
            for item in list_os_versions(limit=2000):
                if item["id"] in missing_os_ids:
                    os_name_by_id[item["id"]] = item["name"]
        except Exception:
            # Если нет доступа к /os-versions или API недоступен — работаем без обогащения.
            pass

    creds_by_os_name: dict[str, SnapshotPasswordEntry] = {}
    try:
        creds_by_os_name = {item["os_version_name"]: item for item in list_snapshot_passwords()}
    except Exception:
        # Нет прав на passwords или endpoint недоступен: оставляем серверные креды как есть.
        creds_by_os_name = {}

    for server in result:
        os_name = str(server.get("os_version") or server.get("os_version_name") or "").strip()
        os_id = server.get("os_version_id")
        if not os_name and isinstance(os_id, int):
            os_name = os_name_by_id.get(os_id, "")
        if not os_name:
            continue

        server["os_version"] = os_name
        creds = creds_by_os_name.get(os_name)
        if not creds:
            continue

        server["server_user"] = creds["ssh_username"]
        server["server_password"] = creds["password"]

    return result


def list_servers(*, enrich: bool = True) -> List[dict]:
    resp = _request("GET", "/manage/", timeout=20)
    data = _parse_json(resp, context="список серверов")
    if not isinstance(data, list):
        raise ServerError("Некорректный ответ сервера: ожидается список серверов.")
    servers = [item for item in data if isinstance(item, dict)]
    if not enrich:
        return servers
    return _enrich_servers_with_snapshot_credentials(servers)


def get_server(server_id: int, *, enrich: bool = True) -> dict:
    resp = _request("GET", f"/manage/{server_id}", timeout=20)
    data = _parse_json(resp, context=f"сервер id={server_id}")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект сервера.")
    if not enrich:
        return data
    enriched = _enrich_servers_with_snapshot_credentials([data])
    return enriched[0] if enriched else _normalize_server_payload(data)


def resolve_server(server_query: str, *, enrich: bool = True) -> dict:
    query = server_query.strip()
    if not query:
        raise ServerError("Не указано имя сервера или номер стенда.")

    servers = list_servers(enrich=enrich)
    if not servers:
        raise ServerError("Список серверов пуст.")

    exact_matches = [
        server for server in servers
        if str(server.get("name") or "").strip().lower() == query.lower()
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ServerError(f"Найдено несколько серверов с именем '{query}'.")

    if query.isdigit():
        stand_matches = [
            server for server in servers
            if _stand_number(str(server.get("name") or "").strip()) == int(query)
        ]
        if len(stand_matches) == 1:
            return stand_matches[0]
        if len(stand_matches) > 1:
            names = ", ".join(
                str(server.get("name") or "").strip()
                for server in sorted(stand_matches, key=server_sort_key)
            )
            raise ServerError(f"Найдено несколько серверов для стенда '{query}': {names}")

    names = ", ".join(
        str(server.get("name") or "").strip()
        for server in sorted(servers, key=server_sort_key)
    )
    raise ServerError(
        f"Сервер '{server_query}' не найден."
        + (f" Доступные серверы: {names}" if names else "")
    )


def resolve_server_id(server_query: str) -> int:
    server = resolve_server(server_query)
    server_id = server.get("id")
    if not isinstance(server_id, int):
        raise ServerError(f"Не удалось определить id сервера '{server_query}'.")
    return server_id


def create_server(payload: dict[str, Any]) -> dict:
    resp = _request("POST", "/manage/", json=payload, timeout=30)
    data = _parse_json(resp, context="создание сервера")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект сервера.")
    return data


def update_server(server_id: int, payload: dict[str, Any]) -> dict:
    resp = _request("PATCH", f"/manage/{server_id}", json=payload, timeout=30)
    data = _parse_json(resp, context=f"обновление сервера id={server_id}")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект сервера.")
    return data


def update_server_os_version_by_name(server_name: str, os_version_name: str) -> dict:
    name = server_name.strip()
    os_name = os_version_name.strip()
    if not name:
        raise ServerError("Не указано имя сервера (server_name).")
    if not os_name:
        raise ServerError("Не указано имя версии ОС (os_version_name).")

    resp = _request(
        "POST",
        "/manage/os-version",
        json={"server_name": name, "os_version_name": os_name},
        timeout=30,
    )
    data = _parse_json(resp, context="обновление версии ОС сервера")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект сервера.")
    return data


def delete_server(server_id: int) -> None:
    _request("DELETE", f"/manage/{server_id}", timeout=20)


def power_on_server(server_id: int) -> dict:
    resp = _request("POST", f"/control/{server_id}/power/on", json={}, timeout=30)
    data = _parse_json(resp, context=f"включение сервера id={server_id}")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект сервера.")
    return data


def power_off_server(server_id: int) -> dict:
    resp = _request("POST", f"/control/{server_id}/power/off", json={}, timeout=30)
    data = _parse_json(resp, context=f"выключение сервера id={server_id}")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект сервера.")
    return data


def reboot_server(server_id: int) -> dict:
    resp = _request("POST", f"/control/{server_id}/power/reboot", json={}, timeout=30)
    data = _parse_json(resp, context=f"перезагрузка сервера id={server_id}")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект сервера.")
    return data


def release_server(server_id: int) -> dict:
    resp = _request("POST", f"/manage/{server_id}/release", json={}, timeout=20)
    data = _parse_json(resp, context=f"освобождение сервера id={server_id}")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект сервера.")
    return data


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


def _normalize_snapshot_password(item: Any, *, context: str) -> SnapshotPasswordEntry:
    if not isinstance(item, dict):
        raise ServerError(f"Некорректный ответ сервера для {context}: ожидается объект.")

    id_value = item.get("id")
    os_version_name = item.get("os_version_name")
    if not isinstance(os_version_name, str) or not os_version_name.strip():
        # Backward compatibility for old API payload.
        os_version_name = item.get("snapshot_name")
    ssh_username = item.get("ssh_username")
    if not isinstance(ssh_username, str) or not ssh_username.strip():
        # Backward compatibility for old API payload.
        ssh_username = item.get("server_user") or "u"
    password = item.get("password")
    updated_by = item.get("updated_by")
    created_at = item.get("created_at")
    updated_at = item.get("updated_at")

    if not isinstance(id_value, int):
        raise ServerError(f"Некорректный ответ сервера для {context}: поле 'id' должно быть числом.")
    if not isinstance(os_version_name, str) or not os_version_name.strip():
        raise ServerError(
            f"Некорректный ответ сервера для {context}: поле 'os_version_name' должно быть строкой."
        )
    if not isinstance(ssh_username, str) or not ssh_username.strip():
        raise ServerError(
            f"Некорректный ответ сервера для {context}: поле 'ssh_username' должно быть строкой."
        )
    if not isinstance(password, str):
        raise ServerError(f"Некорректный ответ сервера для {context}: поле 'password' должно быть строкой.")
    if updated_by is not None and not isinstance(updated_by, str):
        raise ServerError(f"Некорректный ответ сервера для {context}: поле 'updated_by' должно быть строкой или null.")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ServerError(f"Некорректный ответ сервера для {context}: поле 'created_at' должно быть строкой.")
    if not isinstance(updated_at, str) or not updated_at.strip():
        raise ServerError(f"Некорректный ответ сервера для {context}: поле 'updated_at' должно быть строкой.")

    return {
        "id": id_value,
        "os_version_name": os_version_name.strip(),
        "ssh_username": ssh_username.strip(),
        "password": password,
        "updated_by": updated_by.strip() if isinstance(updated_by, str) else None,
        "created_at": created_at.strip(),
        "updated_at": updated_at.strip(),
    }


def list_snapshot_passwords() -> list[SnapshotPasswordEntry]:
    resp = _request("GET", "/passwords/", timeout=20)
    data = _parse_json(resp, context="список паролей снимков")
    if not isinstance(data, list):
        raise ServerError("Некорректный ответ сервера: ожидается список.")
    return [_normalize_snapshot_password(item, context="/passwords") for item in data]


def get_snapshot_password(os_version_name: str) -> SnapshotPasswordEntry:
    name = os_version_name.strip()
    if not name:
        raise ServerError("Не указано имя версии ОС (os_version_name).")

    url = _api_url(f"/passwords/{name}")
    try:
        resp = requests.get(url, headers=_headers(), timeout=20, allow_redirects=False)
    except requests.RequestException as e:
        raise ServerError(f"Не удалось получить пароль для версии ОС '{name}': {e}") from e

    if resp.status_code == 404:
        raise SnapshotPasswordNotFound(name)
    if 300 <= resp.status_code < 400:
        raise ServerError(f"Redirect {resp.status_code} to {resp.headers.get('location') or '?'} for {url}")
    if resp.status_code >= 400:
        detail = _response_detail(resp)
        if detail:
            raise ServerError(f"HTTP {resp.status_code}: {detail}")
        raise ServerError(f"HTTP {resp.status_code}: {resp.text}")

    data = _parse_json(resp, context=f"/passwords/{name}")
    return _normalize_snapshot_password(data, context=f"/passwords/{name}")


def upsert_snapshot_password(os_version_name: str, password: str, ssh_username: str) -> SnapshotPasswordEntry:
    name = os_version_name.strip()
    pwd = password.strip()
    user = ssh_username.strip()
    if not name:
        raise ServerError("Не указано имя версии ОС (os_version_name).")
    if not pwd:
        raise ServerError("Не указан пароль версии ОС (password).")
    if not user:
        raise ServerError("Не указан SSH пользователь версии ОС (ssh_username).")

    resp = _request(
        "POST",
        "/passwords/",
        json={"os_version_name": name, "ssh_username": user, "password": pwd},
        timeout=20,
    )
    data = _parse_json(resp, context="/passwords")
    return _normalize_snapshot_password(data, context="/passwords")


def update_snapshot_password(
    os_version_name: str,
    password: str | None = None,
    ssh_username: str | None = None,
) -> SnapshotPasswordEntry:
    name = os_version_name.strip()
    pwd = password.strip() if password is not None else None
    user = ssh_username.strip() if ssh_username is not None else None
    if not name:
        raise ServerError("Не указано имя версии ОС (os_version_name).")
    if pwd is not None and not pwd:
        raise ServerError("Не указан пароль версии ОС (password).")
    if user is not None and not user:
        raise ServerError("Не указан SSH пользователь версии ОС (ssh_username).")
    if pwd is None and user is None:
        raise ServerError("Нужно указать password и/или ssh_username.")

    url = _api_url(f"/passwords/{name}")
    payload: dict[str, str] = {}
    if pwd is not None:
        payload["password"] = pwd
    if user is not None:
        payload["ssh_username"] = user
    try:
        resp = requests.patch(
            url,
            headers=_headers(),
            json=payload,
            timeout=20,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        raise ServerError(f"Не удалось обновить пароль для версии ОС '{name}': {e}") from e

    if resp.status_code == 404:
        raise SnapshotPasswordNotFound(name)
    if 300 <= resp.status_code < 400:
        raise ServerError(f"Redirect {resp.status_code} to {resp.headers.get('location') or '?'} for {url}")
    if resp.status_code >= 400:
        detail = _response_detail(resp)
        if detail:
            raise ServerError(f"HTTP {resp.status_code}: {detail}")
        raise ServerError(f"HTTP {resp.status_code}: {resp.text}")

    data = _parse_json(resp, context=f"/passwords/{name}")
    return _normalize_snapshot_password(data, context=f"/passwords/{name}")


def delete_snapshot_password(os_version_name: str) -> None:
    name = os_version_name.strip()
    if not name:
        raise ServerError("Не указано имя версии ОС (os_version_name).")

    url = _api_url(f"/passwords/{name}")
    try:
        resp = requests.delete(url, headers=_headers(), timeout=20, allow_redirects=False)
    except requests.RequestException as e:
        raise ServerError(f"Не удалось удалить пароль для версии ОС '{name}': {e}") from e

    if resp.status_code == 404:
        raise SnapshotPasswordNotFound(name)
    if 300 <= resp.status_code < 400:
        raise ServerError(f"Redirect {resp.status_code} to {resp.headers.get('location') or '?'} for {url}")
    if resp.status_code >= 400:
        detail = _response_detail(resp)
        if detail:
            raise ServerError(f"HTTP {resp.status_code}: {detail}")
        raise ServerError(f"HTTP {resp.status_code}: {resp.text}")


def refresh_os_versions() -> OSVersionsSyncEntry:
    resp = _request("POST", "/os-versions/refresh", json={}, timeout=60)
    data = _parse_json(resp, context="/os-versions/refresh")
    if not isinstance(data, dict):
        raise ServerError("Некорректный ответ сервера: ожидается объект.")

    source_url = data.get("source_url")
    added = data.get("added")
    total = data.get("total")
    if not isinstance(source_url, str) or not source_url.strip():
        raise ServerError("Некорректный ответ сервера: поле 'source_url' должно быть строкой.")
    if not isinstance(added, int):
        raise ServerError("Некорректный ответ сервера: поле 'added' должно быть числом.")
    if not isinstance(total, int):
        raise ServerError("Некорректный ответ сервера: поле 'total' должно быть числом.")

    return {
        "source_url": source_url.strip(),
        "added": added,
        "total": total,
    }


def exec_ssh(server: dict) -> None:
    ip = str(server.get("ip_address") or "").strip()
    user = str(
        server.get("server_user")
        or server.get("ssh_username")
        or server.get("username")
        or server.get("user")
        or "u"
    ).strip() or "u"
    password = str(server.get("server_password") or "").strip()
    port = str(server.get("ssh_port") or "22").strip() or "22"

    if not ip:
        raise ServerError("У сервера отсутствует ip_address.")

    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        raise ServerError("Не найден ssh. Установите openssh-client.")

    ssh_args = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-p",
        port,
        f"{user}@{ip}",
    ]

    sshpass_bin = shutil.which("sshpass")
    if sshpass_bin and password and password != "***hidden***":
        os.execvp(
            sshpass_bin,
            [
                "sshpass",
                "-p",
                password,
                *ssh_args,
            ],
        )

    os.execvp(ssh_bin, ssh_args)


def _raise_server_error(resp: requests.Response) -> None:
    try:
        data = resp.json()
        if isinstance(data, dict) and "detail" in data:
            raise ServerError(f"HTTP {resp.status_code}: {data['detail']}")
    except ValueError:
        pass
    raise ServerError(f"HTTP {resp.status_code}: {resp.text}")
