from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
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


def _run_privileged(args: list[str]) -> tuple[int, str]:
    command = list(args)
    try:
        is_root = os.geteuid() == 0
    except AttributeError:
        is_root = False
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


def _find_virsh_domain(name: str) -> tuple[str, str]:
    candidates = _candidate_domain_names(name)
    for uri in ("qemu:///system", "qemu:///session"):
        domains = _virsh_list_domains(uri=uri, running_only=False)
        if domains is None:
            continue
        for candidate in candidates:
            if candidate in domains:
                return uri, candidate

        rc, text = _run_virsh(["virsh", "--connect", uri, "list", "--all"])
        if rc != 0:
            continue
        table_domains = _parse_virsh_table_domains(text)
        for candidate in candidates:
            if candidate in table_domains:
                return uri, candidate
    raise RuntimeError(f"Домен libvirt для ВМ '{name}' не найден.")


def _virsh_dumpxml(*, uri: str, domain_name: str) -> str:
    rc, text = _run_virsh(["virsh", "--connect", uri, "dumpxml", "--inactive", domain_name])
    if rc != 0:
        rc, text = _run_virsh(["virsh", "--connect", uri, "dumpxml", domain_name])
    if rc != 0:
        raise RuntimeError(text or f"virsh dumpxml {domain_name} завершился с кодом {rc}")
    return text


def _virsh_define_xml(*, uri: str, domain_name: str, xml_text: str) -> None:
    tmp_path = Path("/tmp") / f"allta-{domain_name}-domain.xml"
    tmp_path.write_text(xml_text, encoding="utf-8")
    try:
        rc, text = _run_virsh(["virsh", "--connect", uri, "define", str(tmp_path)])
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    if rc != 0:
        raise RuntimeError(text or f"virsh define {domain_name} завершился с кодом {rc}")


def _virsh_restart_domain(*, uri: str, domain_name: str) -> None:
    running = _virsh_list_domains(uri=uri, running_only=True)
    if running is None or domain_name in running:
        _virsh_destroy_domain(uri=uri, domain_name=domain_name)
    _virsh_start_domain(uri=uri, domain_name=domain_name)


def _virsh_destroy_domain(*, uri: str, domain_name: str) -> None:
    rc, text = _run_virsh(["virsh", "--connect", uri, "destroy", domain_name])
    if rc != 0 and "not running" not in text.lower():
        raise RuntimeError(text or f"virsh destroy {domain_name} завершился с кодом {rc}")


def _virsh_start_domain(*, uri: str, domain_name: str) -> None:
    rc, text = _run_virsh(["virsh", "--connect", uri, "start", domain_name])
    if rc != 0:
        raise RuntimeError(text or f"virsh start {domain_name} завершился с кодом {rc}")


def _xml_set_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    child.text = text
    return child


def _domain_primary_disk_path(*, uri: str, domain_name: str) -> str:
    disks = _virsh_domain_disk_paths(uri=uri, domain_name=domain_name)
    if not disks:
        raise RuntimeError(f"Для {domain_name} не найден файловый диск через virsh domblklist.")
    return disks[0]


def _qemu_img_virtual_size_bytes(path: str) -> int | None:
    rc, text = _run_privileged(["qemu-img", "info", "--output=json", path])
    if rc != 0:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    value = payload.get("virtual-size")
    if isinstance(value, int):
        return value
    return None


def _all_attached_disk_paths() -> dict[str, str]:
    result: dict[str, str] = {}
    for uri in ("qemu:///system", "qemu:///session"):
        domains = _virsh_list_domains(uri=uri, running_only=False)
        if not domains:
            continue
        for domain_name in domains:
            for disk_path in _virsh_domain_disk_paths(uri=uri, domain_name=domain_name):
                result[disk_path] = domain_name
    return result


def _disk_path_is_free(path: str, *, target_domain: str | None = None) -> tuple[bool, str]:
    attached = _all_attached_disk_paths()
    owner = attached.get(path)
    if owner is None or owner == target_domain:
        return True, ""
    return False, owner


def _next_disk_target(*, uri: str, domain_name: str) -> str:
    xml_text = _virsh_dumpxml(uri=uri, domain_name=domain_name)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"Некорректный XML домена {domain_name}: {e}") from e

    used: set[str] = set()
    for target in root.findall("./devices/disk/target"):
        dev = str(target.get("dev") or "").strip()
        if dev:
            used.add(dev)
    for letter_ord in range(ord("b"), ord("z") + 1):
        candidate = f"vd{chr(letter_ord)}"
        if candidate not in used:
            return candidate
    raise RuntimeError(f"Не удалось подобрать свободное имя диска для {domain_name}.")


def _update_inventory_record(name: str, updates: dict[str, Any]) -> None:
    vms = _load_vm_state()
    record = vms.get(name)
    if not isinstance(record, dict):
        record = {"name": name}
        vms[name] = record
    record.update(updates)
    record["updated_at"] = _utc_now()
    _save_vm_state(vms)


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

        snapshots = _load_snapshot_state()
        changed = False
        for name in names:
            snaps = snapshots.setdefault(name, [])
            if "build" not in snaps:
                snaps.append("build")
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

    def edit(self, vm: str, *, cpu: int | None = None, ram: int | None = None, disk_size: int | None = None) -> dict[str, Any]:
        names = self._assert_vms_exist([vm])
        name = names[0]
        if cpu is None and ram is None and disk_size is None:
            raise ValueError("Укажите хотя бы один параметр: --cpu, --ram или --disk.")
        if cpu is not None and cpu < 1:
            raise ValueError("Параметр --cpu должен быть >= 1.")
        if ram is not None and ram < 2:
            raise ValueError("Параметр --ram должен быть >= 2 (в GB).")
        if disk_size is not None and disk_size < 1:
            raise ValueError("Параметр --disk должен быть >= 1 (в GB).")

        uri, domain_name = _find_virsh_domain(name)
        changes: list[str] = []

        if cpu is not None or ram is not None:
            xml_text = _virsh_dumpxml(uri=uri, domain_name=domain_name)
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError as e:
                raise RuntimeError(f"Некорректный XML домена {domain_name}: {e}") from e

            if cpu is not None:
                vcpu_node = _xml_set_text(root, "vcpu", str(cpu))
                vcpu_node.set("current", str(cpu))
                changes.append(f"cpu={cpu}")

            if ram is not None:
                ram_kib = ram * 1024 * 1024
                memory_node = _xml_set_text(root, "memory", str(ram_kib))
                memory_node.set("unit", "KiB")
                current_node = _xml_set_text(root, "currentMemory", str(ram_kib))
                current_node.set("unit", "KiB")
                changes.append(f"ram={ram}G")

            _virsh_define_xml(
                uri=uri,
                domain_name=domain_name,
                xml_text=ET.tostring(root, encoding="unicode"),
            )

        resized_disk = ""
        destroyed_for_resize = False
        if disk_size is not None:
            resized_disk = _domain_primary_disk_path(uri=uri, domain_name=domain_name)
            requested_bytes = disk_size * 1024 * 1024 * 1024
            current_bytes = _qemu_img_virtual_size_bytes(resized_disk)
            if current_bytes is not None and requested_bytes < current_bytes:
                current_gb = current_bytes / (1024 * 1024 * 1024)
                raise ValueError(
                    "Уменьшение диска не поддерживается: "
                    f"текущий размер {current_gb:.1f}G, запрошено {disk_size}G."
                )
            if current_bytes != requested_bytes:
                _virsh_destroy_domain(uri=uri, domain_name=domain_name)
                destroyed_for_resize = True
                rc, text = _run_privileged(["qemu-img", "resize", resized_disk, f"{disk_size}G"])
                if rc != 0:
                    _virsh_start_domain(uri=uri, domain_name=domain_name)
                    raise RuntimeError(text or f"qemu-img resize {resized_disk} завершился с кодом {rc}")
            changes.append(f"disk={disk_size}G")

        if destroyed_for_resize:
            _virsh_start_domain(uri=uri, domain_name=domain_name)
        else:
            _virsh_restart_domain(uri=uri, domain_name=domain_name)

        updates: dict[str, Any] = {"status": "on"}
        if cpu is not None:
            updates["cpu"] = cpu
        if ram is not None:
            updates["ram"] = ram
        if disk_size is not None:
            updates["disk_size"] = disk_size
        _update_inventory_record(name, updates)

        return {
            "vm": name,
            "domain": domain_name,
            "uri": uri,
            "changes": changes,
            "resized_disk": resized_disk,
            "restarted": True,
        }

    def disk_create(
        self,
        vm: str,
        *,
        path: str,
        size: int,
        target: str | None = None,
        format_name: str = "qcow2",
    ) -> dict[str, Any]:
        disk_path = str(path or "").strip()
        if not _is_safe_disk_path(disk_path):
            raise ValueError("Путь диска должен быть абсолютным файлом qcow2/qcow/img/raw/vmdk/vdi.")
        if Path(disk_path).exists():
            raise ValueError(f"Диск уже существует: {disk_path}")
        if size < 1:
            raise ValueError("Параметр --size должен быть >= 1 (в GB).")

        parent = Path(disk_path).parent
        if not parent.exists():
            raise ValueError(f"Каталог для диска не существует: {parent}")

        rc, text = _run_privileged(["qemu-img", "create", "-f", format_name, disk_path, f"{size}G"])
        if rc != 0:
            raise RuntimeError(text or f"qemu-img create {disk_path} завершился с кодом {rc}")

        try:
            return self.disk_attach(vm, path=disk_path, target=target, format_name=format_name)
        except Exception:
            try:
                Path(disk_path).unlink()
            except OSError:
                pass
            raise

    def disk_attach(
        self,
        vm: str,
        *,
        path: str,
        target: str | None = None,
        format_name: str = "qcow2",
    ) -> dict[str, Any]:
        names = self._assert_vms_exist([vm])
        name = names[0]
        disk_path = str(path or "").strip()
        if not _is_safe_disk_path(disk_path):
            raise ValueError("Путь диска должен быть абсолютным файлом qcow2/qcow/img/raw/vmdk/vdi.")
        if not Path(disk_path).exists():
            raise ValueError(f"Диск не найден: {disk_path}")

        uri, domain_name = _find_virsh_domain(name)
        is_free, owner = _disk_path_is_free(disk_path, target_domain=domain_name)
        if not is_free:
            raise ValueError(f"Диск уже подключён к VM '{owner}': {disk_path}")

        target_dev = str(target or "").strip() or _next_disk_target(uri=uri, domain_name=domain_name)
        running = _virsh_list_domains(uri=uri, running_only=True)
        mode_flag = "--persistent" if running is None or domain_name in running else "--config"
        command = [
            "virsh",
            "--connect",
            uri,
            "attach-disk",
            domain_name,
            disk_path,
            target_dev,
            "--targetbus",
            "virtio",
            "--driver",
            "qemu",
            "--subdriver",
            format_name,
            mode_flag,
        ]
        rc, text = _run_virsh(command)
        if rc != 0:
            raise RuntimeError(text or f"virsh attach-disk {domain_name} {disk_path} завершился с кодом {rc}")

        _virsh_restart_domain(uri=uri, domain_name=domain_name)

        vms = _load_vm_state()
        record = vms.get(name)
        if not isinstance(record, dict):
            record = {"name": name}
            vms[name] = record
        disks = record.get("extra_disks")
        if not isinstance(disks, list):
            disks = []
        if disk_path not in [str(item.get("path") if isinstance(item, dict) else item) for item in disks]:
            disks.append({"path": disk_path, "target": target_dev, "format": format_name})
        record["extra_disks"] = disks
        record["status"] = "on"
        record["updated_at"] = _utc_now()
        _save_vm_state(vms)

        return {
            "vm": name,
            "domain": domain_name,
            "uri": uri,
            "disk": disk_path,
            "target": target_dev,
            "format": format_name,
            "restarted": True,
        }

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
