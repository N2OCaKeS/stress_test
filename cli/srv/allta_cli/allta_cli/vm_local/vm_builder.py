from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from allta_cli.utils.allta_runtime import get_libvirt, get_libvirt_manager

Libvirt = get_libvirt()
LibvirtManager = get_libvirt_manager()

STATE_DIR = Path.home() / ".config" / "allta" / "local_vm"
VM_STATE_FILE = STATE_DIR / "vms.json"
SNAPSHOT_STATE_FILE = STATE_DIR / "snapshots.json"
PREPARE_STATE_FILE = STATE_DIR / "prepare.json"
PROVIDER_VM_STATE_FILE = STATE_DIR / "provider_vms_dates.json"

DEFAULT_LOCAL_USER = "u"
DEFAULT_LOCAL_PASSWORD = "1"
DEFAULT_LOCAL_SSH_PORT = "22"
RELEASES_URL = "http://allta.devos.astralinux.ru/rest/api/get-repo-path"


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
        "disk_size": disk_size if disk_size is not None else raw.get("disk_size"),
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


def _fetch_releases_map(timeout: int = 20) -> dict[str, Any]:
    req = Request(RELEASES_URL, headers={"User-Agent": "allta-cli/1 local-vm"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as e:
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
            build_params[name] = {
                "cpu": self.cpu,
                # allta_lib/virt-install ожидает память в MB.
                "ram": self.ram * 1024,
                "disk_size": self.disk_size,
            }

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
