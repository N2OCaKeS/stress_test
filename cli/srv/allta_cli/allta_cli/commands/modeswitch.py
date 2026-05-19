from __future__ import annotations

import os
import subprocess
from shutil import which

from allta_cli.utils import ui


MODE_ORYOL = "o"
MODE_SMOLENSK = "s"

MODE_ALIASES = {
    "o": MODE_ORYOL,
    "0": MODE_ORYOL,
    "oryol": MODE_ORYOL,
    "eagle": MODE_ORYOL,
    "орёл": MODE_ORYOL,
    "орел": MODE_ORYOL,
    "s": MODE_SMOLENSK,
    "2": MODE_SMOLENSK,
    "smolensk": MODE_SMOLENSK,
    "смоленск": MODE_SMOLENSK,
}

MODE_TITLES = {
    MODE_ORYOL: "Орёл (astra-modeswitch=0)",
    MODE_SMOLENSK: "Смоленск (astra-modeswitch=2)",
}

MODE_COMMANDS: dict[str, list[tuple[str, list[str]]]] = {
    MODE_ORYOL: [
        ("astra-modeswitch", ["set", "0"]),
    ],
    MODE_SMOLENSK: [
        ("astra-modeswitch", ["set", "2"]),
        ("astra-mic-control", ["enable"]),
        ("astra-mac-control", ["enable"]),
    ],
}


_SBIN_DIRS = ("/usr/local/sbin", "/usr/sbin", "/sbin")


def _which(tool: str) -> str | None:
    found = which(tool)
    if found:
        return found
    for d in _SBIN_DIRS:
        p = os.path.join(d, tool)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _sudo() -> str:
    try:
        if os.geteuid() == 0:
            return ""
    except AttributeError:
        return ""
    return "sudo " if which("sudo") else ""


def _run(cmd: str, fail: str) -> int:
    ui.cmd(cmd)
    rc = subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL).returncode
    if rc != 0:
        ui.err(f"{fail} (код {rc})")
    return rc


def modeswitch_cmd(mode: str) -> int:
    normalized = MODE_ALIASES.get(mode.strip().lower()) if mode else None
    if not normalized:
        ui.err(f"Неизвестный режим '{mode}'. Используйте 'o' (Орёл) или 's' (Смоленск).")
        return 1

    ui.echo(f"Целевой режим: {MODE_TITLES[normalized]}")

    sudo = _sudo()
    for tool, args in MODE_COMMANDS[normalized]:
        if not _which(tool):
            ui.err(f"Не найдена утилита '{tool}'. Команда поддерживается только на Astra Linux SE.")
            return 1
        cmd = f"{sudo}{tool} {' '.join(args)}"
        rc = _run(cmd, f"выполнение {tool}")
        if rc != 0:
            return rc

    ui.ok(f"Режим переключён: {MODE_TITLES[normalized]}.")
    ui.warn("Для применения нового режима необходимо перезагрузить ПК.")
    return 0
