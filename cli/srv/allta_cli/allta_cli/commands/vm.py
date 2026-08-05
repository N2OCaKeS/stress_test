from __future__ import annotations

import re
from typing import Dict, Any, List

from allta_cli.utils import auth
from allta_cli.utils.config import VM_API_BASE
from allta_cli.utils.http_fallback import request_with_http_fallback
from allta_cli.utils.lazy import requests


class VMError(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    token = auth.load_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _api_url(path: str) -> str:
    return f"{VM_API_BASE}{path}"


def _request(method: str, path: str, *, params=None, json=None, timeout=30) -> requests.Response:
    url = _api_url(path)
    resp = request_with_http_fallback(
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
        raise VMError(f"Redirect {resp.status_code} to {loc or '?'} for {url}")
    if resp.status_code >= 400:
        _raise_vm_error(resp)
    return resp


def _parse_json(resp: requests.Response, *, context: str) -> Any:
    try:
        return resp.json()
    except ValueError as e:
        raise VMError(f"Некорректный ответ сервера для {context}: {resp.text}") from e


def _looks_like_server_query(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.isdigit():
        return True
    # New format: stand12_srv-main (or stand12-srv-main).
    if re.match(r"^(?i:stand)\d+[-_].+", stripped):
        return True
    # Legacy format: 12-srv-main.
    parts = stripped.split("-", 1)
    return len(parts) == 2 and parts[0].isdigit() and bool(parts[1].strip())


_STAND_ONLY_RE = re.compile(r"^(?i:stand)(\d+)$")
_VM_STAND_NAME_RE = re.compile(r"^\s*(?:stand)?(?P<stand>\d+)[-_]", flags=re.IGNORECASE)


def _stand_query_number(value: str) -> int | None:
    """`12` или `stand12` → 12; иначе None."""
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)
    m = _STAND_ONLY_RE.match(stripped)
    return int(m.group(1)) if m else None


def _vm_stand_number_from_name(name: str) -> int | None:
    match = _VM_STAND_NAME_RE.match(name)
    if not match:
        return None
    return int(match.group("stand"))


def _get_vm_by_stand_no(stand_no: int) -> dict:
    matches = [
        vm for vm in list_vms()
        if _vm_stand_number_from_name(str(vm.get("name") or "")) == stand_no
    ]
    if not matches:
        raise VMError(f"ВМ со стендом #{stand_no} не найдена.")
    if len(matches) > 1:
        names = ", ".join(str(vm.get("name") or "").strip() for vm in matches)
        raise VMError(
            f"Найдено несколько ВМ со стендом #{stand_no}: {names}. "
            "Укажите имя ВМ явно."
        )
    return matches[0]


def list_vms() -> List[dict]:
    r = _request("GET", "/vm/", params={"skip": 0, "limit": 100}, timeout=20)
    data = _parse_json(r, context="список ВМ")
    if not isinstance(data, list):
        raise VMError("Некорректный ответ сервера: ожидается список ВМ.")
    return [item for item in data if isinstance(item, dict)]


def get_vm_by_name(name: str) -> dict:
    vms = list_vms()
    for vm in vms:
        if vm.get("name") == name:
            return vm
    raise VMError(f"ВМ '{name}' не найдена")


def resolve_vm_query(query: str) -> dict:
    value = query.strip()
    if not value:
        raise VMError("Не указано имя ВМ или номер стенда.")

    vm: dict | None = None
    vm_by_name_error: Exception | None = None
    try:
        vm = get_vm_by_name(value)
    except Exception as e:
        vm_by_name_error = e

    if vm is None:
        stand_no = _stand_query_number(value)
        if stand_no is not None:
            vm = _get_vm_by_stand_no(stand_no)

    if vm is None and _looks_like_server_query(value):
        vm = _get_vm_by_server_query(value)

    if vm is None:
        assert vm_by_name_error is not None
        raise vm_by_name_error
    return vm


def resolve_vm_name(query: str) -> str:
    vm = resolve_vm_query(query)
    name = str(vm.get("name") or "").strip()
    if not name:
        raise VMError(f"Не удалось определить имя ВМ для '{query}'.")
    return name


def create_vms(server_id: int, password: str, vm_specs: List[str]) -> dict:
    payload_vms: Dict[str, Dict[str, Any]] = {}
    for item in vm_specs:
        try:
            name, ip, cpu, ram = item.split(":")
            payload_vms[name] = {"cpu": int(cpu), "ram": int(ram), "ip": ip}
        except ValueError as e:
            raise VMError(f"Неверный формат VM '{item}'. Используйте NAME:IP:CPU:RAM") from e

    payload = {
        "server_id": server_id,
        "ip_range_id": 1,
        "password": password,
        "vms": payload_vms,
    }
    r = _request("POST", "/vm/create", json=payload, timeout=30)
    data = _parse_json(r, context="создание ВМ")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


def status_vm(name: str) -> dict:
    vm = resolve_vm_query(name)
    return {"name": vm.get("name"), "status": vm.get("status"), "password": vm.get("password")}


def status_set_vm(name: str) -> dict:
    vm = resolve_vm_query(name)
    vm_id = vm.get("id")
    if not vm_id:
        raise VMError("Не удалось определить id ВМ.")
    login = auth.current_login()
    payload = {"status": login}
    response = _request("PATCH", f"/vm/{vm_id}/status", json=payload, timeout=20)
    if not response.content:
        return {}
    data = _parse_json(response, context=f"обновление статуса ВМ id={vm_id}")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


def status_free_vm(name: str) -> dict:
    vm = resolve_vm_query(name)
    vm_id = vm.get("id")
    if not vm_id:
        raise VMError("Не удалось определить id ВМ.")
    response = _request("POST", f"/vm/{vm_id}/release", json={}, timeout=20)
    if not response.content:
        return {}
    data = _parse_json(response, context=f"освобождение ВМ id={vm_id}")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


def astra_update(rc: str, vm_names: List[str]) -> dict:
    payload = {
        "rc": rc,
        "names": vm_names,
    }
    r = _request("POST", "/vm/astra-update", json=payload, timeout=30)
    data = _parse_json(r, context="astra-update")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


def start_vms(vm_names: List[str]) -> dict:
    payload = {"names": vm_names}
    r = _request("POST", "/vm/start", json=payload, timeout=20)
    data = _parse_json(r, context="старт ВМ")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


def stop_vms(vm_names: List[str]) -> dict:
    payload = {"names": vm_names}
    r = _request("POST", "/vm/stop", json=payload, timeout=20)
    data = _parse_json(r, context="стоп ВМ")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


# -------- Snapshots ----------

def list_snapshots(vm_name: str) -> List[dict]:
    r = _request("GET", "/snapshot/", params={"vm_name": vm_name}, timeout=20)
    data = _parse_json(r, context=f"список snapshots vm={vm_name}")
    if not isinstance(data, list):
        raise VMError("Некорректный ответ сервера: ожидается список snapshots.")
    return [item for item in data if isinstance(item, dict)]


def create_snapshot(vm_names: List[str], snap_name: str) -> dict:
    payload = {"names": vm_names, "snapshot": snap_name}
    r = _request("POST", "/snapshot/create", json=payload, timeout=30)
    data = _parse_json(r, context="создание snapshot")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


def delete_snapshot(vm_names: List[str], snap_name: str) -> dict:
    payload = {"names": vm_names, "snapshot": snap_name}
    r = _request("DELETE", "/snapshot/delete", json=payload, timeout=30)
    data = _parse_json(r, context="удаление snapshot")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


def revert_snapshot(vm_names: List[str], snap_name: str) -> dict:
    payload = {"names": vm_names, "snapshot": snap_name}
    r = _request("POST", "/snapshot/revert", json=payload, timeout=30)
    data = _parse_json(r, context="откат snapshot")
    if not isinstance(data, dict):
        raise VMError("Некорректный ответ сервера: ожидается объект.")
    return data


def _get_vm_by_server_query(server_query: str) -> dict:
    from allta_cli.commands import server as server_api

    server = server_api.resolve_server(server_query)
    server_id = server.get("id")
    if not isinstance(server_id, int):
        raise VMError(f"У сервера '{server_query}' отсутствует корректный id.")

    vms = list_vms()
    matches = [
        vm for vm in vms
        if str(vm.get("server_id")) == str(server_id)
    ]
    server_name = str(server.get("name") or server_query).strip()

    if not matches:
        raise VMError(f"Для сервера '{server_name}' (id={server_id}) не найдены ВМ.")
    if len(matches) > 1:
        names = ", ".join(
            str(vm.get("name") or "").strip() or "<unnamed>"
            for vm in matches
        )
        raise VMError(
            f"Для сервера '{server_name}' найдено несколько ВМ: {names}. "
            "Укажите имя ВМ явно."
        )
    return matches[0]


def resolve_vm_ssh_target(name: str) -> dict:
    query = name.strip()
    if not query:
        raise VMError("Не указано имя ВМ или сервера.")

    vm = resolve_vm_query(query)

    ip = str(vm.get("ip_address") or "").strip()
    user = str(vm.get("server_user") or vm.get("username") or vm.get("user") or "u").strip() or "u"
    password = str(vm.get("password") or vm.get("server_password") or "").strip()
    port = str(vm.get("ssh_port") or "22").strip() or "22"

    if not ip:
        raise VMError(f"У ВМ '{vm.get('name') or query}' отсутствует ip_address.")

    return {
        "name": vm.get("name") or query,
        "ip_address": ip,
        "server_user": user,
        "server_password": password,
        "ssh_port": port,
    }


def _raise_vm_error(resp: requests.Response) -> None:
    try:
        data = resp.json()
        if isinstance(data, dict) and "detail" in data:
            raise VMError(f"HTTP {resp.status_code}: {data['detail']}")
    except ValueError:
        pass
    raise VMError(f"HTTP {resp.status_code}: {resp.text}")
