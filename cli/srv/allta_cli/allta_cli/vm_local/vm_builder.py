from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests

from allta_cli.utils.allta_runtime import get_libvirt, get_libvirt_manager
from allta_cli.utils.http_fallback import request_with_http_fallback

Libvirt = get_libvirt()
LibvirtManager = get_libvirt_manager()

STATE_DIR = Path.home() / ".config" / "allta" / "local_vm"
VM_STATE_FILE = STATE_DIR / "vms.json"
SNAPSHOT_STATE_FILE = STATE_DIR / "snapshots.json"
PREPARE_STATE_FILE = STATE_DIR / "prepare.json"
PROVIDER_VM_STATE_FILE = STATE_DIR / "provider_vms_dates.json"
SNAPSHOT_LIST_STATE_FILES = (
    STATE_DIR / "snapshot_list.json",
    STATE_DIR / "snapshots_list.json",
    STATE_DIR / "all_snapshots.json",
    STATE_DIR / "provider_snapshots.json",
)

DEFAULT_LOCAL_USER = "u"
DEFAULT_LOCAL_PASSWORD = "1"
DEFAULT_LOCAL_SSH_PORT = "22"
RELEASES_URL = "https://allta.devos.astralinux.ru/rest/api/get-repo-path"
VM_DISK_DIRS = (
    Path("/vms"),
    Path("/var/lib/libvirt/images"),
)
VM_DISK_SUFFIXES = (".qcow2", ".qcow", ".img", ".raw", ".vmdk", ".vdi")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    _ensure_state_dir()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _pick_str(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_vm_record(
    name: str,
    source: Any,
    *,
    cpu: int | None = None,
    ram: int | None = None,
    disk_size: int | None = None,
    box: str | None = None,
    rc: str | None = None,
) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}

    ip_address = _pick_str(raw, ("ip_address", "ip", "ipv4", "host", "hostname"))
    ip_bridge = _pick_str(raw, ("ip_bridge", "bridge_ip", "ip_bridge_address"))
    bridge = _pick_str(raw, ("bridge", "bridge_name", "network_bridge"))
    status = _pick_str(raw, ("status", "state"))

    record = {
        "name": name,
        "ip_address": ip_address,
        "ip_bridge": ip_bridge,
        "bridge": bridge,
        "cpu": cpu if cpu is not None else raw.get("cpu"),
        "ram": ram if ram is not None else raw.get("ram"),
        "disk_size": disk_size if disk_size is not None else raw.get("disk"),
        "box": box or _pick_str(raw, ("box",)),
        "rc": rc or _pick_str(raw, ("rc", "release")),
        "status": status,

        "server_user": DEFAULT_LOCAL_USER,
        "server_password": DEFAULT_LOCAL_PASSWORD,
        "ssh_port": DEFAULT_LOCAL_SSH_PORT,
        "updated_at": _utc_now(),
        "raw": raw,
    }
    return record


def _load_vm_state() -> dict[str, dict[str, Any]]:
    payload = _read_json(VM_STATE_FILE, default={})
    if not isinstance(payload, dict):
        return {}
    raw_vms = payload.get("vms", payload)
    if not isinstance(raw_vms, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for name, vm_info in raw_vms.items():
        vm_name = str(name).strip()
        if not vm_name:
            continue
        if isinstance(vm_info, dict):
            result[vm_name] = vm_info
    return result


def _save_vm_state(vms: dict[str, dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "updated_at": _utc_now(),
        "vms": vms,
    }
    _write_json(VM_STATE_FILE, payload)


def _load_snapshot_state() -> dict[str, list[str]]:
    payload = _read_json(SNAPSHOT_STATE_FILE, default={})
    if not isinstance(payload, dict):
        return {}
    raw_data = payload.get("snapshots", payload)
    if not isinstance(raw_data, dict):
        return {}

    result: dict[str, list[str]] = {}
    for name, items in raw_data.items():
        vm_name = str(name).strip()
        if not vm_name:
            continue
        if not isinstance(items, list):
            continue
        uniq: list[str] = []
        seen: set[str] = set()
        for item in items:
            snap_name = str(item).strip()
            if not snap_name or snap_name in seen:
                continue
            seen.add(snap_name)
            uniq.append(snap_name)
        result[vm_name] = uniq
    return result


def _save_snapshot_state(snapshots: dict[str, list[str]]) -> None:
    payload = {
        "version": 1,
        "updated_at": _utc_now(),
        "snapshots": snapshots,
    }
    _write_json(SNAPSHOT_STATE_FILE, payload)


def _item_belongs_to_vm(item: Any, vm_names: set[str]) -> bool:
    if isinstance(item, str):
        text = item.strip()
        return text in vm_names or any(text.startswith(f"{name}:") for name in vm_names)

    if isinstance(item, dict):
        for key in ("vm", "vm_name", "name_vm", "domain", "domain_name", "host"):
            value = item.get(key)
            if value is not None and str(value).strip() in vm_names:
                return True
        name = item.get("name")
        if name is not None and str(name).strip() in vm_names:
            return True
    return False


def _remove_vms_from_snapshot_payload(payload: Any, vm_names: set[str]) -> tuple[Any, bool]:
    if isinstance(payload, dict):
        changed = False
        result: dict[str, Any] = {}
        for key, value in payload.items():
            key_name = str(key).strip()
            if key_name in vm_names:
                changed = True
                continue
            if key_name in {"snapshots", "items", "data", "vms"}:
                new_value, nested_changed = _remove_vms_from_snapshot_payload(value, vm_names)
                result[key] = new_value
                changed = changed or nested_changed
                continue
            if _item_belongs_to_vm(value, vm_names):
                changed = True
                continue
            result[key] = value
        return result, changed

    if isinstance(payload, list):
        result = [item for item in payload if not _item_belongs_to_vm(item, vm_names)]
        return result, len(result) != len(payload)

    return payload, False


def _cleanup_snapshot_list_files(vm_names: set[str]) -> list[str]:
    changed_files: list[str] = []
    for path in SNAPSHOT_LIST_STATE_FILES:
        if not path.exists() or not path.is_file():
            continue
        payload = _read_json(path, default=None)
        if payload is None:
            continue
        cleaned_payload, changed = _remove_vms_from_snapshot_payload(payload, vm_names)
        if not changed:
            continue
        _write_json(path, cleaned_payload)
        changed_files.append(path.name)
    return changed_files


def _is_prepare_completed() -> bool:
    payload = _read_json(PREPARE_STATE_FILE, default={})
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("prepared"))


def _mark_prepare_completed() -> None:
    payload = {
        "version": 1,
        "updated_at": _utc_now(),
        "prepared": True,
    }
    _write_json(PREPARE_STATE_FILE, payload)


def _load_provider_vms_data() -> dict[str, Any]:
    path = PROVIDER_VM_STATE_FILE
    if not path.exists() or not path.is_file():
        return {}
    data = LibvirtManager.Vm.load_vms_data(save_path=str(path))
    if not isinstance(data, dict):
        return {}
    return data


def _save_provider_vms_data(vms_dates: dict[str, Any]) -> None:
    _ensure_state_dir()
    LibvirtManager.Vm.save_vms_data(vms_dates=vms_dates, save_path=str(PROVIDER_VM_STATE_FILE))


def _dedupe_names(vm_names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in vm_names:
        name = str(item).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _build_provider_vm_params(*, cpu: int, ram: int, disk_size: int) -> dict[str, Any]:
    # allta 1.1.8 использует ключ `disk` для qemu-img resize.
    return {
        "cpu": cpu,
        # allta_lib/virt-install ожидает память в MB.
        "ram": ram * 1024,
        "disk": disk_size,
    }


def _fetch_releases_map(timeout: int = 20) -> dict[str, Any]:
    try:
        resp = request_with_http_fallback(
            "GET",
            RELEASES_URL,
            headers={"User-Agent": "allta-cli/1 local-vm"},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.text
    except requests.RequestException as e:
        raise RuntimeError(f"Не удалось получить releases: {e}") from e
    except OSError as e:
        raise RuntimeError(f"Ошибка сети при получении releases: {e}") from e

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Некорректный JSON releases: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError("Некорректный формат releases: ожидается JSON-объект.")
    return data


def _normalize_sources_lines(raw: Any) -> list[str]:
    if isinstance(raw, list):
        lines = [str(item).strip() for item in raw]
        return [line for line in lines if line]
    if isinstance(raw, str):
        lines = [line.strip() for line in raw.splitlines()]
        return [line for line in lines if line]
    return []


def _sources_for_rc(rc: str) -> str:
    rc_name = str(rc).strip()
    if not rc_name:
        raise ValueError("Версия rc не указана.")

    releases = _fetch_releases_map()
    if rc_name not in releases:
        raise ValueError(f"Нет записи для релиза '{rc_name}' в releases.")

    lines = _normalize_sources_lines(releases.get(rc_name))
    if not lines:
        raise ValueError(f"Для релиза '{rc_name}' список репозиториев пуст.")
    # Нужны реальные переводы строк, чтобы файл sources.list был валидным.
    return "\n".join(lines)


def _build_target_names(vm_name: str, vm_count: int) -> list[str]:
    base_name = str(vm_name).strip()
    if not base_name:
        raise ValueError("Базовое имя ВМ не указано.")
    if vm_count < 1:
        raise ValueError("vm_count должен быть >= 1")

    sanitized = base_name.replace("_", "-")
    if vm_count == 1:
        return [sanitized]
    return [f"{sanitized}-{idx}" for idx in range(1, vm_count + 1)]


def _run_process(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    output = (proc.stdout or "").strip()
    errors = (proc.stderr or "").strip()
    return proc.returncode, output if output else errors


def _run_virsh(args: list[str]) -> tuple[int, str]:
    command = list(args)
    try:
        is_root = os.geteuid() == 0
    except AttributeError:
        is_root = False

    # Для получения статуса system-доменов virsh должен выполняться через sudo.
    if not is_root:
        sudo_bin = shutil.which("sudo")
        if sudo_bin:
            command = [sudo_bin, "-n", *command]

    return _run_process(command)


def _virsh_list_domains(*, uri: str, running_only: bool) -> set[str] | None:
    args = ["virsh", "--connect", uri, "list", "--name"]
    if not running_only:
        args.append("--all")
    rc, text = _run_virsh(args)
    if rc != 0:
        return None
    return {line.strip() for line in text.splitlines() if line.strip()}


def _parse_virsh_table_domains(text: str) -> set[str]:
    result: set[str] = set()
    for line in str(text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit():
            result.add(parts[1])
        elif len(parts) >= 2 and parts[0] == "-":
            result.add(parts[1])
    return result


def _virsh_all_domains() -> set[str]:
    domains: set[str] = set()
    saw_output = False
    for uri in ("qemu:///system", "qemu:///session"):
        names = _virsh_list_domains(uri=uri, running_only=False)
        if names is not None:
            saw_output = True
            domains.update(names)

        rc, text = _run_virsh(["virsh", "--connect", uri, "list", "--all"])
        if rc == 0:
            saw_output = True
            domains.update(_parse_virsh_table_domains(text))

    if not saw_output:
        raise RuntimeError("Не удалось получить список VM через virsh list --all.")
    return domains


def _domain_exists_in_virsh(name: str) -> bool:
    candidates = set(_candidate_domain_names(name))
    return bool(candidates.intersection(_virsh_all_domains()))


def _domain_name_in_text(name: str, text: str) -> bool:
    candidates = set(_candidate_domain_names(name))
    return any(candidate in _parse_virsh_table_domains(text) for candidate in candidates)


def _virsh_domain_in_table_output(name: str) -> bool | None:
    saw_output = False
    saw_error = False
    for uri in ("qemu:///system", "qemu:///session"):
        rc, text = _run_virsh(["virsh", "--connect", uri, "list", "--all"])
        if rc != 0:
            saw_error = True
            continue
        saw_output = True
        if _domain_name_in_text(name, text):
            return True

    if saw_output and not saw_error:
        return False
    return None


def _provider_vm_list_contains(name: str) -> bool | None:
    try:
        text = LibvirtManager.Vm.vm_list()
    except Exception:
        return None
    if text is None:
        return None
    return _domain_name_in_text(name, str(text))


def _virsh_list_snapshots(*, uri: str, domain_name: str) -> list[str] | None:
    rc, text = _run_virsh(["virsh", "--connect", uri, "snapshot-list", domain_name, "--name"])
    if rc != 0:
        return None
    return [line.strip() for line in text.splitlines() if line.strip()]


def _is_safe_disk_path(path: str) -> bool:
    disk_path = str(path or "").strip()
    if not disk_path.startswith("/"):
        return False
    if disk_path.startswith(("/dev/", "/proc/", "/sys/", "/run/")):
        return False
    return Path(disk_path).suffix.lower() in VM_DISK_SUFFIXES


def _path_name_matches_vm(path: str, vm_name: str) -> bool:
    stem = Path(str(path or "").strip()).stem
    candidates = _candidate_domain_names(vm_name)
    return any(candidate and candidate in stem for candidate in candidates)


def _collect_disk_paths_from_value(value: Any, vm_name: str, result: list[str], seen: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if isinstance(item, str) and any(token in key_text for token in ("disk", "image", "path", "source", "device")):
                disk_path = item.strip()
                if _is_safe_disk_path(disk_path) and _path_name_matches_vm(disk_path, vm_name) and disk_path not in seen:
                    seen.add(disk_path)
                    result.append(disk_path)
                continue
            _collect_disk_paths_from_value(item, vm_name, result, seen)
        return

    if isinstance(value, list):
        for item in value:
            _collect_disk_paths_from_value(item, vm_name, result, seen)


def _guessed_disk_paths(vm_name: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for candidate in _candidate_domain_names(vm_name):
        for disk_dir in VM_DISK_DIRS:
            for suffix in VM_DISK_SUFFIXES:
                disk_path = str(disk_dir / f"{candidate}{suffix}")
                if disk_path in seen:
                    continue
                seen.add(disk_path)
                result.append(disk_path)
    return result


def _disk_paths_from_inventory(vm_name: str, record: Any) -> list[str]:
    result = _guessed_disk_paths(vm_name)
    seen = set(result)
    _collect_disk_paths_from_value(record, vm_name, result, seen)
    return result


def _virsh_domain_disk_paths(*, uri: str, domain_name: str) -> list[str]:
    rc, text = _run_virsh(["virsh", "--connect", uri, "domblklist", domain_name, "--details"])
    if rc != 0:
        return []

    result: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[0] != "file" or parts[1] != "disk":
            continue
        disk_path = parts[-1].strip()
        if not _is_safe_disk_path(disk_path) or disk_path in seen:
            continue
        seen.add(disk_path)
        result.append(disk_path)
    return result


def _delete_disk_files(paths: list[str]) -> str:
    for disk_path in paths:
        if not _is_safe_disk_path(disk_path):
            continue
        command = ["rm", "-f", "--", disk_path]
        try:
            is_root = os.geteuid() == 0
        except AttributeError:
            is_root = False
        if not is_root:
            sudo_bin = shutil.which("sudo")
            if sudo_bin:
                command = [sudo_bin, "-n", *command]
        rc, text = _run_process(command)
        if rc != 0:
            return text or f"Не удалось удалить диск {disk_path}: rm завершился с кодом {rc}"
    return ""


def _virsh_domain_exists(name: str) -> bool | None:
    candidates = set(_candidate_domain_names(name))
    saw_virsh = False
    saw_error = False
    for uri in ("qemu:///system", "qemu:///session"):
        domains = _virsh_list_domains(uri=uri, running_only=False)
        if domains is None:
            saw_error = True
            continue
        saw_virsh = True
        if candidates.intersection(domains):
            return True
    table_exists = _virsh_domain_in_table_output(name)
    if table_exists is True:
        return True

    provider_exists = _provider_vm_list_contains(name)
    if provider_exists is True:
        return True

    if saw_virsh and not saw_error:
        return False
    if table_exists is False and provider_exists is False:
        return False
    return None


def _candidate_domain_names(name: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for candidate in (
        name,
        name.replace("_", "-"),
        name.replace("-", "_"),
    ):
        candidate_name = str(candidate).strip()
        if not candidate_name or candidate_name in seen:
            continue
        seen.add(candidate_name)
        result.append(candidate_name)
    return result


def _power_status_from_domstate(raw_state: str) -> str:
    text = str(raw_state or "").strip().lower()
    if not text:
        return "unknown"

    off_tokens = (
        "shut off",
        "in shutdown",
        "shutdown",
        "выключ",
        "останов",
        "не запущ",
        "заверш",
        "poweroff",
    )
    on_tokens = (
        "running",
        "paused",
        "idle",
        "работ",
        "запущ",
        "выполня",
        "приостанов",
    )
    if any(token in text for token in off_tokens):
        return "off"
    if any(token in text for token in on_tokens):
        return "on"
    return "unknown"


def _cleanup_vm_state(vm_names: list[str]) -> list[str]:
    names = _dedupe_names(vm_names)
    if not names:
        return []
    name_set = set(names)
    removed: set[str] = set()

    vms = _load_vm_state()
    remaining_vms = {name: data for name, data in vms.items() if name not in name_set}
    removed.update(name for name in vms if name in name_set)
    if remaining_vms != vms:
        _save_vm_state(remaining_vms)

    snapshots = _load_snapshot_state()
    remaining_snapshots = {name: data for name, data in snapshots.items() if name not in name_set}
    removed.update(name for name in snapshots if name in name_set)
    if remaining_snapshots != snapshots:
        _save_snapshot_state(remaining_snapshots)
    if _cleanup_snapshot_list_files(name_set):
        removed.update(names)

    try:
        provider_data = _load_provider_vms_data()
    except Exception:
        provider_data = {}
    if isinstance(provider_data, dict):
        remaining_provider = {
            name: data for name, data in provider_data.items() if str(name).strip() not in name_set
        }
        removed.update(str(name).strip() for name in provider_data if str(name).strip() in name_set)
        if remaining_provider != provider_data:
            _save_provider_vms_data(remaining_provider)

    return [name for name in names if name in removed]


def _delete_domain_snapshots_with_virsh(*, uri: str, domain_name: str) -> str:
    snapshots = _virsh_list_snapshots(uri=uri, domain_name=domain_name)
    if snapshots is None:
        return f"Не удалось получить список snapshot'ов для {domain_name}"
    if not snapshots:
        return ""

    remaining = list(snapshots)
    while remaining:
        snapshot_name = remaining[0]
        delete_commands = (
            ["virsh", "--connect", uri, "snapshot-delete", domain_name, snapshot_name, "--children"],
            ["virsh", "--connect", uri, "snapshot-delete", domain_name, snapshot_name, "--children", "--metadata"],
            ["virsh", "--connect", uri, "snapshot-delete", domain_name, snapshot_name],
            ["virsh", "--connect", uri, "snapshot-delete", domain_name, snapshot_name, "--metadata"],
        )
        last_error = ""
        deleted = False
        for command in delete_commands:
            rc, text = _run_virsh(command)
            if rc == 0:
                deleted = True
                break
            last_error = text or f"{' '.join(command)} завершился с кодом {rc}"
        if not deleted:
            return f"Не удалось удалить snapshot '{snapshot_name}' для {domain_name}: {last_error}"

        refreshed = _virsh_list_snapshots(uri=uri, domain_name=domain_name)
        if refreshed is None:
            return f"Не удалось проверить snapshot'ы после удаления '{snapshot_name}' для {domain_name}"
        if refreshed == remaining:
            return f"Snapshot '{snapshot_name}' для {domain_name} не удалился"
        remaining = refreshed

    return ""


def _delete_domain_with_virsh(name: str) -> tuple[bool, str]:
    candidates = _candidate_domain_names(name)
    saw_any_domain = False
    saw_error = False
    saw_success = False
    last_error = ""

    for uri in ("qemu:///system", "qemu:///session"):
        domains = _virsh_list_domains(uri=uri, running_only=False)
        if domains is None:
            domains = set()
            saw_error = True
        else:
            saw_success = True

        rc, text = _run_virsh(["virsh", "--connect", uri, "list", "--all"])
        if rc == 0:
            saw_success = True
            domains.update(_parse_virsh_table_domains(text))

        existing = [candidate for candidate in candidates if candidate in domains]
        if not existing:
            continue

        saw_any_domain = True
        running = _virsh_list_domains(uri=uri, running_only=True)
        for domain_name in existing:
            disk_paths = _virsh_domain_disk_paths(uri=uri, domain_name=domain_name)
            snapshot_error = _delete_domain_snapshots_with_virsh(uri=uri, domain_name=domain_name)
            if snapshot_error:
                last_error = snapshot_error
                continue

            if running is None or domain_name in running:
                rc, text = _run_virsh(["virsh", "--connect", uri, "destroy", domain_name])
                if rc != 0:
                    last_error = text or f"virsh destroy {domain_name} завершился с кодом {rc}"

            undefine_commands = (
                ["virsh", "--connect", uri, "undefine", domain_name, "--remove-all-storage", "--nvram"],
                ["virsh", "--connect", uri, "undefine", domain_name, "--remove-all-storage"],
                ["virsh", "--connect", uri, "undefine", domain_name],
            )
            for command in undefine_commands:
                rc, text = _run_virsh(command)
                if rc == 0:
                    disk_error = _delete_disk_files(disk_paths)
                    if disk_error:
                        return False, disk_error
                    return True, ""
                last_error = text or f"{' '.join(command)} завершился с кодом {rc}"

    if not saw_any_domain:
        if saw_error and not saw_success:
            return False, "Не удалось проверить libvirt через virsh. Проверьте доступ к sudo virsh."
        return False, ""
    return False, last_error


def _query_vm_power_status(vm_name: str, running_domains: set[str] | None = None) -> str:
    name = str(vm_name).strip()
    if not name:
        return "unknown"

    del running_domains  # kept for backward-compatible call sites

    candidates = _candidate_domain_names(name)
    for uri in ("qemu:///system", "qemu:///session"):
        running = _virsh_list_domains(uri=uri, running_only=True)
        all_domains = _virsh_list_domains(uri=uri, running_only=False)
        if running is None or all_domains is None:
            continue
        if any(candidate in running for candidate in candidates):
            return "on"
        if any(candidate in all_domains for candidate in candidates):
            return "off"

    # Fallback: если list недоступен, пробуем domstate для каждого варианта имени.
    for uri in ("qemu:///system", "qemu:///session"):
        for candidate in candidates:
            for args in (
                ["virsh", "--connect", uri, "domstate", "--reason", candidate],
                ["virsh", "--connect", uri, "domstate", candidate],
            ):
                rc, text = _run_virsh(args)
                if rc != 0:
                    continue
                state = _power_status_from_domstate(text)
                if state != "unknown":
                    return state
    return "unknown"


def _seed_vm_state_from_provider_if_empty() -> dict[str, dict[str, Any]]:
    current = _load_vm_state()
    if current:
        return current

    try:
        provider_data = _load_provider_vms_data()
    except Exception:
        return {}
    if not isinstance(provider_data, dict) or not provider_data:
        return {}

    seeded: dict[str, dict[str, Any]] = {}
    for name, item in provider_data.items():
        vm_name = str(name).strip()
        if not vm_name:
            continue
        seeded[vm_name] = _normalize_vm_record(vm_name, item)

    if seeded:
        _save_vm_state(seeded)
    return seeded


class VmBuilder:
    def __init__(
        self,
        vm_name: str,
        vm_count: int,
        cpu: int,
        ram: int,
        disk_size: int,
        rc: str,
        box: str | None = None,
    ):
        self.vm_name = vm_name
        self.vm_count = vm_count
        self.cpu = cpu
        self.ram = ram
        self.disk_size = disk_size
        self.provider = Libvirt()
        self.vms_dates: dict[str, Any] = {}
        self.rc = rc
        self.box = str(box or "").strip()
        if not self.box or self.box == "local":
            if self.rc.startswith("1.7"):
                self.box = "1.7.5.o"
            elif self.rc.startswith("1.8"):
                self.box = "1.8.1.o"
            else:
                self.box = "local"

    def _target_names(self) -> list[str]:
        return _build_target_names(self.vm_name, self.vm_count)

    def _ensure_names_are_unique(self, names: list[str]) -> None:
        vm_state = _seed_vm_state_from_provider_if_empty()
        existing = set(vm_state.keys())
        try:
            provider_data = _load_provider_vms_data()
        except Exception:
            provider_data = {}
        if isinstance(provider_data, dict):
            existing.update(str(name).strip() for name in provider_data.keys() if str(name).strip())
        duplicates = [name for name in names if name in existing]
        if duplicates:
            dupes = ", ".join(duplicates)
            raise ValueError(f"Нельзя создать ВМ с уже существующим именем: {dupes}")

    def prepare(self) -> bool:
        if _is_prepare_completed():
            return False
        result = self.provider.prepare()
        if isinstance(result, int) and result != 0:
            raise RuntimeError(f"Libvirt.prepare завершился с кодом {result}")
        _mark_prepare_completed()
        return True

    def build(self) -> list[str]:
        names = self._target_names()
        self._ensure_names_are_unique(names)

        build_params: dict[str, dict[str, Any]] = {}
        for name in names:
            build_params[name] = _build_provider_vm_params(
                cpu=self.cpu,
                ram=self.ram,
                disk_size=self.disk_size,
            )

        self.vms_dates = self.provider.build(
            box=self.box,
            rc=self.rc,
            vms=names,
            vms_dates=build_params,
        )

        _save_provider_vms_data(self.vms_dates)

        vm_state = _load_vm_state()
        for name in names:
            provider_vm = self.vms_dates.get(name, build_params.get(name, {}))
            vm_state[name] = _normalize_vm_record(
                name=name,
                source=provider_vm,
                cpu=self.cpu,
                ram=self.ram,
                disk_size=self.disk_size,
                box=self.box,
                rc=self.rc,
            )
        _save_vm_state(vm_state)

        snapshots = _load_snapshot_state()
        changed = False
        for name in names:
            if name not in snapshots:
                snapshots[name] = []
                changed = True
        if changed:
            _save_snapshot_state(snapshots)

        return names


class VmManager:
    def __init__(self):
        self.provider = LibvirtManager()
        try:
            self.vms_dates = _load_provider_vms_data()
        except Exception:
            self.vms_dates = {}
        _seed_vm_state_from_provider_if_empty()

    def _known_vms(self) -> dict[str, dict[str, Any]]:
        return _seed_vm_state_from_provider_if_empty() or _load_vm_state()

    def _assert_vms_exist(self, vms: list[str]) -> list[str]:
        names = _dedupe_names(vms)
        if not names:
            raise ValueError("Список ВМ пуст.")
        known = self._known_vms()
        missing = [name for name in names if name not in known]
        if missing:
            raise ValueError(f"ВМ не найдены: {', '.join(missing)}")
        return names

    def _set_status_for_vms(self, vm_names: list[str], status: str) -> None:
        vms = self._known_vms()
        changed = False
        for name in vm_names:
            record = vms.get(name)
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or "") == status:
                continue
            record["status"] = status
            record["updated_at"] = _utc_now()
            changed = True
        if changed:
            _save_vm_state(vms)

    def list_vm_records(self) -> list[dict[str, Any]]:
        vms = self._known_vms()
        changed = False
        rows: list[dict[str, Any]] = []

        for name in sorted(vms.keys()):
            source = vms.get(name, {})
            row = dict(source) if isinstance(source, dict) else {"name": name}
            power_status = _query_vm_power_status(name)
            if power_status != "unknown":
                row["status"] = power_status
                if isinstance(source, dict) and str(source.get("status") or "") != power_status:
                    source["status"] = power_status
                    source["updated_at"] = _utc_now()
                    changed = True
            rows.append(row)

        if changed:
            _save_vm_state(vms)
        return rows

    def get_vm_record(self, vm_name: str) -> dict[str, Any]:
        name = str(vm_name).strip()
        if not name:
            raise ValueError("Имя ВМ не указано.")
        vms = self._known_vms()
        if name not in vms:
            raise ValueError(f"ВМ '{name}' не найдена.")
        source = vms[name]
        result = dict(source) if isinstance(source, dict) else {"name": name}
        power_status = _query_vm_power_status(name)
        if power_status != "unknown":
            result["status"] = power_status
            if isinstance(source, dict) and str(source.get("status") or "") != power_status:
                source["status"] = power_status
                source["updated_at"] = _utc_now()
                _save_vm_state(vms)
        return result

    def start(self, vms: list[str]):
        names = self._assert_vms_exist(vms)
        self.provider.Vm.start(vms=names)
        self._set_status_for_vms(names, "on")

    def stop(self, vms: list[str]):
        names = self._assert_vms_exist(vms)
        self.provider.Vm.stop(vms=names)
        self._set_status_for_vms(names, "off")

    def delete(
        self,
        vms: list[str] | None = None,
        *,
        all_vms: bool = False,
        force: bool = False,
    ) -> dict[str, list[str]]:
        known = self._known_vms()
        try:
            provider_known = _load_provider_vms_data()
        except Exception:
            provider_known = {}
        if not isinstance(provider_known, dict):
            provider_known = {}
        if all_vms:
            try:
                virsh_domains = _virsh_all_domains()
            except RuntimeError as e:
                raise RuntimeError(f"Не удалось получить список VM для --all: {e}") from e

            if force:
                names = sorted(virsh_domains)
            else:
                names = []
                for name in sorted(known.keys()):
                    candidates = set(_candidate_domain_names(name))
                    if candidates.intersection(virsh_domains):
                        names.append(name)
        else:
            requested = _dedupe_names(vms or [])
            if force:
                names = requested
            else:
                missing_from_inventory = [name for name in requested if name not in known]
                if missing_from_inventory:
                    raise ValueError(
                        "ВМ не найдены в local inventory: "
                        f"{', '.join(missing_from_inventory)}. "
                        "Для удаления домена без записи в inventory используйте --force."
                    )
                names = requested

        if not names:
            if all_vms:
                return {"deleted": [], "missing": [], "cleaned": [], "failed": []}
            raise ValueError("Список ВМ пуст.")

        deleted: list[str] = []
        missing: list[str] = []
        failed: list[str] = []
        failed_errors: list[str] = []

        for name in names:
            removed_from_host, error = _delete_domain_with_virsh(name)
            if error:
                failed.append(name)
                failed_errors.append(f"{name}: {error}")
                continue
            if removed_from_host:
                deleted.append(name)
            else:
                disk_error = _delete_disk_files(
                    _disk_paths_from_inventory(
                        name,
                        {
                            "inventory": known.get(name, {}),
                            "provider": provider_known.get(name, {}),
                        },
                    )
                )
                if disk_error:
                    failed.append(name)
                    failed_errors.append(f"{name}: {disk_error}")
                    continue
                missing.append(name)

        cleanup_candidates = [name for name in names if name not in set(failed)]
        cleaned = _cleanup_vm_state(cleanup_candidates)
        result = {
            "deleted": deleted,
            "missing": missing,
            "cleaned": cleaned,
            "failed": failed,
        }
        if failed_errors:
            result["errors"] = failed_errors
        return result

    def clear_missing(self) -> dict[str, list[str]]:
        known = self._known_vms()
        if not known:
            return {"kept": [], "cleaned": []}

        virsh_domains = _virsh_all_domains()
        kept: list[str] = []
        stale: list[str] = []
        for name in sorted(known.keys()):
            candidates = set(_candidate_domain_names(name))
            if candidates.intersection(virsh_domains):
                kept.append(name)
            else:
                stale.append(name)

        cleaned = _cleanup_vm_state(stale)
        return {
            "kept": kept,
            "cleaned": cleaned,
        }

    def snapshots(self, vm: str) -> list[str]:
        vm_info = self.get_vm_record(vm)
        name = str(vm_info["name"])
        data = _load_snapshot_state()
        return list(data.get(name, []))

    def _assert_snapshots_absent(self, vms: list[str], snapshot_name: str) -> None:
        data = _load_snapshot_state()
        duplicates = [name for name in vms if snapshot_name in data.get(name, [])]
        if duplicates:
            raise ValueError(
                f"Снимок '{snapshot_name}' уже существует для ВМ: {', '.join(duplicates)}"
            )

    def _assert_snapshots_present(self, vms: list[str], snapshot_name: str) -> None:
        data = _load_snapshot_state()
        missing = [name for name in vms if snapshot_name not in data.get(name, [])]
        if missing:
            raise ValueError(
                f"Снимок '{snapshot_name}' не найден для ВМ: {', '.join(missing)}"
            )

    def snapshot_create(self, vms: list[str], snapshot_name: str):
        names = self._assert_vms_exist(vms)
        snap_name = str(snapshot_name).strip()
        if not snap_name:
            raise ValueError("Имя снимка не указано.")

        self._assert_snapshots_absent(names, snap_name)
        self.provider.Snapshot.create(vms=names, snapshot_name=snap_name)

        data = _load_snapshot_state()
        for name in names:
            snaps = data.setdefault(name, [])
            snaps.append(snap_name)
        _save_snapshot_state(data)

    def snapshot_delete(self, vms: list[str], snapshot_name: str):
        names = self._assert_vms_exist(vms)
        snap_name = str(snapshot_name).strip()
        if not snap_name:
            raise ValueError("Имя снимка не указано.")

        self._assert_snapshots_present(names, snap_name)
        self.provider.Snapshot.delete(vms=names, snapshot_name=snap_name)

        data = _load_snapshot_state()
        for name in names:
            data[name] = [item for item in data.get(name, []) if item != snap_name]
        _save_snapshot_state(data)

    def snapshot_revert(self, vms: list[str], snapshot_name: str):
        names = self._assert_vms_exist(vms)
        snap_name = str(snapshot_name).strip()
        if not snap_name:
            raise ValueError("Имя снимка не указано.")

        self._assert_snapshots_present(names, snap_name)
        self.provider.Snapshot.revert(vms=names, snapshot_name=snap_name)

    def astra_update(self, vms: list[str], rc: str):
        names = self._assert_vms_exist(vms)
        rc_name = str(rc).strip()
        if not rc_name:
            raise ValueError("Версия rc не указана.")

        repo_sources = _sources_for_rc(rc_name)
        self._assert_snapshots_absent(names, rc_name)
        self.provider.Snapshot.revert(vms=names, snapshot_name="build")
        set_repo_cmd = (
            f"printf '%s\\n' {shlex.quote(repo_sources)} "
            "| sudo tee /etc/apt/sources.list >/dev/null"
        )

        group = {"all": names}
        command = {
            "g_all": {
                "repo": {
                    "command": set_repo_cmd,
                    "signal set": "repo",
                },
                "update": {
                    "command": "sudo apt-get update",
                    "signal get": ["repo"],
                    "signal set": "updated",
                },
                "astra update": {
                    "command": "sudo astra-update -A -T -r",
                    "signal get": ["updated"],
                    "signal set": "astra",
                },
                "reboot": {"signal get": ["astra"]},
            }
        }
        Libvirt.execute(commands=command, vms_dates=self.vms_dates, vms_groups=group)
        self.provider.Snapshot.create(vms=names, snapshot_name=rc_name)

        data = _load_snapshot_state()
        for name in names:
            snaps = data.setdefault(name, [])
            snaps.append(rc_name)
        _save_snapshot_state(data)
