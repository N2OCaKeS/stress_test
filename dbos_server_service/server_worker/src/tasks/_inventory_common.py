"""Общий сбор hardware-inventory и OS-пользователей поверх `TargetRunner`.

Набор команд и капчур сырых блоков живут тут (по образцу `_packages_common`),
а разбор сырых facts в callback-payload остаётся в `services.ssh_client`
(`inventory_facts_to_payload` / `os_users_facts_to_payload`) — общий для сервера
и ВМ. Транспорт спрятан за примитивом `run(cmd)`, поэтому цель может быть и
прямым сервером (`DirectRunner`), и гостём ВМ через hub (`GuestHopRunner`).

Сейчас поверх этого кода работает VM-инвентаризация (`vms_inventory`). Серверная
`inventory.sync` пока ходит через `SshClient.get_inventory`; `collect_inventory`
здесь повторяет её набор команд и имена/порядок блоков байт-в-байт, поэтому
серверную сторону можно унифицировать одним шагом — делегировать
`ssh_client.collect_inventory`/`collect_os_users` в этот модуль (правка в
`services/ssh_client.py`).
"""

from __future__ import annotations

import json

from src.clients.ssh import SshError, _parse_os_release


async def _capture_text(runner, command: str) -> dict:
    """Выполнить команду через `runner`, вернуть `{stdout, stderr, returncode}`.

    При non-zero — `{error, returncode, stdout}`; при `SshError` — `{error,
    returncode: None}`. Не raise'ит: caller собирает частичный inventory, если
    одна команда не отработала (нет файла, нет утилиты). Зеркало
    `SshClient._capture_text`, но поверх `runner.run`.
    """
    try:
        rc, out, err = await runner.run(command)
    except SshError as exc:
        return {"error": f"{exc.error_code}: {exc.message}", "returncode": None}
    if rc != 0:
        return {"error": err.strip() or f"exit code {rc}", "returncode": rc, "stdout": out}
    return {"stdout": out.strip(), "stderr": err.strip(), "returncode": rc}


async def _capture_json(runner, command: str) -> dict:
    """То же, что `_capture_text`, но stdout парсится в JSON.

    Битый JSON → `{error, raw_stdout}` (обрезанный), чтобы caller видел, что
    пришло. Зеркало `SshClient._capture_json`.
    """
    text = await _capture_text(runner, command)
    if "error" in text:
        return text
    raw = text.get("stdout", "")
    try:
        return {"data": json.loads(raw)}
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": f"invalid JSON: {exc}", "raw_stdout": raw[:512]}


async def collect_inventory(runner) -> dict:
    """Собрать структурированный inventory цели через `runner`.

    Возврат — dict с блоками `hostname`, `kernel`, `cpu`, `disks`, `df`,
    `meminfo`, `net_interfaces`, `virtualization`, `os`, `pci`, `astra_build`,
    `astra_license`, `apt_sources` — тот же shape, что отдаёт
    `SshClient.get_inventory`, поэтому `inventory_facts_to_payload` разбирает его
    без изменений. Каждый блок best-effort: одна упавшая команда не валит остальные.
    """
    facts: dict = {}
    facts["hostname"] = await _capture_text(runner, "hostname")
    facts["kernel"] = await _capture_text(runner, "uname -a")
    facts["cpu"] = await _capture_json(runner, "lscpu -J")
    facts["disks"] = await _capture_json(
        runner, "lsblk -b -J -o NAME,SIZE,TYPE,MODEL,SERIAL,MOUNTPOINT"
    )
    facts["df"] = await _capture_text(
        runner, "df -B1 --output=source,target,size,used,pcent"
    )
    facts["meminfo"] = await _capture_text(runner, "cat /proc/meminfo")
    facts["net_interfaces"] = await _capture_text(runner, "ip -o link show")
    facts["virtualization"] = await _capture_text(
        runner,
        "if [ -e /dev/kvm ] || grep -qE '(vmx|svm)' /proc/cpuinfo; "
        "then echo 1; else echo 0; fi",
    )

    os_release_raw = await _capture_text(runner, "cat /etc/os-release")
    facts["os"] = (
        _parse_os_release(os_release_raw.get("stdout", ""))
        if isinstance(os_release_raw, dict) else {}
    )
    if isinstance(os_release_raw, dict) and "error" in os_release_raw:
        facts["os"]["error"] = os_release_raw["error"]

    lspci_raw = await _capture_text(runner, "lspci -mm")
    if isinstance(lspci_raw, dict):
        lines = [ln for ln in lspci_raw.get("stdout", "").splitlines() if ln.strip()]
        facts["pci"] = {"devices": lines}
        if "error" in lspci_raw:
            facts["pci"]["error"] = lspci_raw["error"]

    facts["astra_build"] = await _capture_text(runner, "cat /etc/astra/build_version")
    facts["astra_license"] = await _capture_text(runner, "cat /etc/astra_license")
    facts["apt_sources"] = await _capture_text(
        runner,
        "cat /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null",
    )
    return facts


async def collect_os_users(runner) -> dict:
    """Снять список OS-пользователей цели через `runner`.

    Возврат — dict с блоками `passwd`, `group`, `login_defs` — тот же shape, что
    у `SshClient.get_os_users`, разбирает `os_users_facts_to_payload`. sudo
    определяется по членству в группах (`getent group`), отдельного `sudo -l` нет.
    """
    facts: dict = {}
    facts["passwd"] = await _capture_text(runner, "getent passwd")
    facts["group"] = await _capture_text(runner, "getent group")
    facts["login_defs"] = await _capture_text(runner, "cat /etc/login.defs")
    return facts


__all__ = ["collect_inventory", "collect_os_users"]
