from __future__ import annotations

from typing import Any

from allta_cli.utils.runtime_env import system_ld_library_path_scope


class LocalVMError(RuntimeError):
    pass


def _load_vm_runtime():
    try:
        from allta_cli.vm_local import vm_builder as runtime
    except Exception as e:
        raise LocalVMError(f"Не удалось загрузить local VM backend: {e}") from e
    return runtime


def _load_vm_classes():
    runtime = _load_vm_runtime()
    return runtime.VmBuilder, runtime.VmManager


def _local_defaults() -> tuple[str, str, str]:
    runtime = _load_vm_runtime()
    user = str(getattr(runtime, "DEFAULT_LOCAL_USER", "u"))
    password = str(getattr(runtime, "DEFAULT_LOCAL_PASSWORD", "1"))
    port = str(getattr(runtime, "DEFAULT_LOCAL_SSH_PORT", "22"))
    return user, password, port


def _manager():
    _, vm_manager_cls = _load_vm_classes()
    try:
        return vm_manager_cls()
    except Exception as e:
        raise LocalVMError(f"Не удалось инициализировать local VM manager: {e}") from e


def _pick_str(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_legacy_vm(name: str, raw_vm: Any) -> dict[str, Any]:
    raw = raw_vm if isinstance(raw_vm, dict) else {}
    user, password, default_port = _local_defaults()
    return {
        "name": _pick_str(raw, ("name",)) or name,
        "ip_address": _pick_str(raw, ("ip_address", "ip", "ipv4")),
        "ip_bridge": _pick_str(raw, ("ip_bridge", "bridge_ip", "ip_bridge_address")),
        "bridge": _pick_str(raw, ("bridge", "bridge_name")),
        "server_user": user,
        "server_password": password,
        "ssh_port": _pick_str(raw, ("ssh_port", "port")) or default_port,
        "status": _pick_str(raw, ("status", "state")),
        "password": password,
        "raw": raw,
    }


def _normalize_vm_record(raw: dict[str, Any]) -> dict[str, Any]:
    user, password, default_port = _local_defaults()
    record = dict(raw)
    record["name"] = str(record.get("name") or "").strip()
    record["ip_address"] = str(record.get("ip_address") or "").strip()
    record["ip_bridge"] = str(record.get("ip_bridge") or "").strip()
    record["bridge"] = str(record.get("bridge") or "").strip()
    record["status"] = str(record.get("status") or "").strip()
    record["server_user"] = user
    record["server_password"] = password
    record["ssh_port"] = str(record.get("ssh_port") or default_port).strip() or default_port
    record["password"] = password
    return record


def _fallback_target_names(vm_name: str, vm_count: int) -> list[str]:
    base_name = str(vm_name).strip().replace("_", "-")
    if not base_name:
        raise LocalVMError("Базовое имя ВМ не указано.")
    if vm_count < 1:
        raise LocalVMError("Параметр --count должен быть >= 1.")
    if vm_count == 1:
        return [base_name]
    return [f"{base_name}-{idx}" for idx in range(1, vm_count + 1)]


def list_vms() -> list[dict[str, Any]]:
    manager = _manager()

    if hasattr(manager, "list_vm_records"):
        try:
            raw_items = manager.list_vm_records()
        except Exception as e:
            raise LocalVMError(f"Ошибка чтения local VM inventory: {e}") from e

        if not isinstance(raw_items, list):
            raise LocalVMError("Некорректный формат local VM inventory: ожидается список.")

        result: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            vm = _normalize_vm_record(item)
            if vm["name"]:
                result.append(vm)
        return result

    # Legacy fallback: используем vms_dates напрямую.
    data = getattr(manager, "vms_dates", {})
    if not isinstance(data, dict):
        raise LocalVMError("Некорректный формат local VM данных: ожидается словарь.")

    result: list[dict[str, Any]] = []
    for name, raw_vm in data.items():
        result.append(_normalize_legacy_vm(str(name), raw_vm))
    return result


def get_vm_by_name(name: str) -> dict[str, Any]:
    query = name.strip().lower()
    for vm in list_vms():
        if vm.get("name", "").strip().lower() == query:
            return vm
    raise LocalVMError(f"Локальная ВМ '{name}' не найдена")


def status_vm(name: str) -> dict[str, str]:
    vm = get_vm_by_name(name)
    return {
        "name": str(vm.get("name") or ""),
        "status": str(vm.get("status") or ""),
        "password": str(vm.get("password") or ""),
    }


def build_vms(
    *,
    vm_name: str,
    vm_count: int,
    cpu: int,
    ram: int,
    disk_size: int,
    rc: str,
    box: str | None = None,
) -> list[str]:
    if vm_count < 1:
        raise LocalVMError("Параметр --count должен быть >= 1.")
    if cpu < 1:
        raise LocalVMError("Параметр --cpu должен быть >= 1.")
    if ram < 2:
        raise LocalVMError("Параметр --ram должен быть >= 2 (в GB).")
    if disk_size < 11:
        raise LocalVMError("Параметр --disk должен быть >= 11.")

    vm_builder_cls, _ = _load_vm_classes()
    try:
        with system_ld_library_path_scope():
            builder = vm_builder_cls(
                vm_name=vm_name,
                vm_count=vm_count,
                cpu=cpu,
                ram=ram,
                disk_size=disk_size,
                rc=rc,
                box=box,
            )
            builder.prepare()
            built = builder.build()
    except Exception as e:
        raise LocalVMError(f"Ошибка при сборке local VM: {e}") from e

    if isinstance(built, list) and built:
        return [str(name) for name in built]
    return _fallback_target_names(vm_name=vm_name, vm_count=vm_count)


def start_vms(vm_names: list[str]) -> None:
    try:
        with system_ld_library_path_scope():
            _manager().start(vms=vm_names)
    except Exception as e:
        raise LocalVMError(f"Ошибка запуска local VM: {e}") from e


def stop_vms(vm_names: list[str]) -> None:
    try:
        with system_ld_library_path_scope():
            _manager().stop(vms=vm_names)
    except Exception as e:
        raise LocalVMError(f"Ошибка остановки local VM: {e}") from e


def _normalize_snapshots(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []

    if isinstance(raw, list):
        rows: list[dict[str, Any]] = []
        for idx, item in enumerate(raw, start=1):
            if isinstance(item, dict):
                snap_id = item.get("id", idx)
                snap_name = _pick_str(item, ("name",)) or str(snap_id)
            else:
                snap_id = idx
                snap_name = str(item)
            rows.append({"id": snap_id, "name": snap_name})
        return rows

    if isinstance(raw, dict):
        rows: list[dict[str, Any]] = []
        for key, value in raw.items():
            if isinstance(value, dict):
                snap_name = _pick_str(value, ("name",)) or str(key)
            else:
                snap_name = str(value) if str(value).strip() else str(key)
            rows.append({"id": key, "name": snap_name})
        return rows

    return [{"id": 1, "name": str(raw)}]


def list_snapshots(vm_name: str) -> list[dict[str, Any]]:
    try:
        with system_ld_library_path_scope():
            manager = _manager()
            return _normalize_snapshots(manager.snapshots(vm_name))
    except Exception as e:
        raise LocalVMError(f"Ошибка получения снимков local VM: {e}") from e


def create_snapshot(vm_names: list[str], snap_name: str) -> str:
    try:
        with system_ld_library_path_scope():
            _manager().snapshot_create(vms=vm_names, snapshot_name=snap_name)
    except Exception as e:
        raise LocalVMError(f"Ошибка создания snapshot для local VM: {e}") from e
    return "local-task"


def delete_snapshot(vm_names: list[str], snap_name: str) -> str:
    try:
        with system_ld_library_path_scope():
            _manager().snapshot_delete(vms=vm_names, snapshot_name=snap_name)
    except Exception as e:
        raise LocalVMError(f"Ошибка удаления snapshot для local VM: {e}") from e
    return "local-task"


def revert_snapshot(vm_names: list[str], snap_name: str) -> str:
    try:
        with system_ld_library_path_scope():
            _manager().snapshot_revert(vms=vm_names, snapshot_name=snap_name)
    except Exception as e:
        raise LocalVMError(f"Ошибка отката snapshot для local VM: {e}") from e
    return "local-task"


def astra_update(rc: str, vm_names: list[str]) -> str:
    try:
        with system_ld_library_path_scope():
            _manager().astra_update(vms=vm_names, rc=rc)
    except Exception as e:
        raise LocalVMError(f"Ошибка astra-update для local VM: {e}") from e
    return "local-task"


def resolve_vm_ssh_target(name: str) -> dict[str, Any]:
    vm = get_vm_by_name(name)
    user, password, default_port = _local_defaults()
    ip = str(vm.get("ip_address") or vm.get("ip_bridge") or "").strip()
    if not ip:
        raise LocalVMError(f"Для local VM '{name}' не найден IP.")

    return {
        "name": vm.get("name"),
        "ip_address": ip,
        # По требованиям: для local VM всегда u:1
        "server_user": user,
        "server_password": password,
        "ssh_port": str(vm.get("ssh_port") or default_port).strip() or default_port,
    }
