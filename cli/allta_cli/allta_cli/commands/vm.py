from __future__ import annotations

from typing import Dict, Any, List

import requests

from allta_cli.utils import auth
from allta_cli.utils.config import VM_API_BASE


class VMError(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    token = auth.load_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _api_url(path: str) -> str:
    return f"{VM_API_BASE}{path}"


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
        raise VMError(f"Redirect {resp.status_code} to {loc or '?'} for {url}")
    if resp.status_code >= 400:
        _raise_vm_error(resp)
    return resp


def list_vms() -> List[dict]:
    r = _request("GET", "/vm/", params={"skip": 0, "limit": 100}, timeout=20)
    try:
        return r.json()
    except ValueError as e:
        raise VMError(f"Некорректный ответ сервера: {r.text}") from e


def get_vm_by_name(name: str) -> dict:
    vms = list_vms()
    for vm in vms:
        if vm.get("name") == name:
            return vm
    raise VMError(f"ВМ '{name}' не найдена")


def create_vms(server_id: int, password: str, vm_specs: List[str]) -> str:
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
    return r.json().get("task_id", "")


def status_vm(name: str) -> dict:
    vm = get_vm_by_name(name)
    return {"name": vm.get("name"), "status": vm.get("status"), "password": vm.get("password")}


def status_set_vm(name: str) -> None:
    vm = get_vm_by_name(name)
    vm_id = vm.get("id")
    if not vm_id:
        raise VMError("Не удалось определить id ВМ.")
    login = auth.current_login()
    payload = {"status": login}
    _request("PATCH", f"/vm/{vm_id}/status", json=payload, timeout=20)


def astra_update(rc: str, vm_names: List[str]) -> str:
    payload = {
        "rc": rc,
        "names": vm_names,
    }
    r = _request("POST", "/vm/astra-update", json=payload, timeout=30)
    return r.json().get("task_id", "")


def start_vms(vm_names: List[str]) -> str:
    payload = {"names": vm_names}
    r = _request("POST", "/vm/start", json=payload, timeout=20)
    return r.json().get("task_id", "")


def stop_vms(vm_names: List[str]) -> str:
    payload = {"names": vm_names}
    r = _request("POST", "/vm/stop", json=payload, timeout=20)
    return r.json().get("task_id", "")


# -------- Snapshots ----------

def list_snapshots(vm_name: str) -> List[dict]:
    r = _request("GET", "/snapshot/", params={"vm_name": vm_name}, timeout=20)
    return r.json()


def create_snapshot(vm_names: List[str], snap_name: str) -> str:
    payload = {"names": vm_names, "snapshot": snap_name}
    r = _request("POST", "/snapshot/create", json=payload, timeout=30)
    return r.json().get("task_id", "")


def delete_snapshot(vm_names: List[str], snap_name: str) -> str:
    payload = {"names": vm_names, "snapshot": snap_name}
    r = _request("DELETE", "/snapshot/delete", json=payload, timeout=30)
    return r.json().get("task_id", "")


def revert_snapshot(vm_names: List[str], snap_name: str) -> str:
    payload = {"names": vm_names, "snapshot": snap_name}
    r = _request("POST", "/snapshot/revert", json=payload, timeout=30)
    return r.json().get("task_id", "")


def _raise_vm_error(resp: requests.Response) -> None:
    try:
        data = resp.json()
        if isinstance(data, dict) and "detail" in data:
            raise VMError(f"HTTP {resp.status_code}: {data['detail']}")
    except ValueError:
        pass
    raise VMError(f"HTTP {resp.status_code}: {resp.text}")
