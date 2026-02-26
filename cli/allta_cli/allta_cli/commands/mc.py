from __future__ import annotations

import shlex
from shutil import which

from allta import SystemCommands
from allta_cli.utils import ui

_QA_URL = "ftp://10.177.5.111"
_CI_URL = "ftp://10.177.113.135/maintainers"
_MAIN_URL = "ftp://10.177.103.10/"

def _ensure_mc() -> bool:
    if which("mc"):
        return True
    ui.err("Не найден бинарник 'mc'. Установите пакет midnight-commander (mc).")
    return False

def _run_mc(url: str) -> int:
    if not _ensure_mc():
        return 1
    cmd = f"mc {shlex.quote(url)}"
    ui.cmd(cmd)
    return SystemCommands.cmd_with_returncode(command=cmd)

def mc_qa() -> int:
    """Открыть MC на QA FTP."""
    ui.step(f"Открываю MC: {_QA_URL}")
    return _run_mc(_QA_URL)

def mc_ci() -> int:
    """Открыть MC на CI FTP."""
    ui.step(f"Открываю MC: {_CI_URL}")
    return _run_mc(_CI_URL)

def mc_10() -> int:
    """Открыть MC на ftp://10.177.103.10/."""
    ui.step(f"Открываю MC: {_MAIN_URL}")
    return _run_mc(_MAIN_URL)
