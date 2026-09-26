"""Шаги `pam_fix` и `stand_setup` пайплайна подготовки стенда.

`pam_fix` комментирует `pam_lastlog.so inactive=` в `/etc/pam.d/common-auth`
(если флаг профиля включён). `stand_setup` дописывает параметры ядра в
`GRUB_CMDLINE_LINUX_DEFAULT` и гонит произвольный bash-скрипт теста через
stdin `bash -s` — на диске стенда скрипт не остаётся.

Форматы `stand_setup`/`provisioning` — см. `schemas/prepare_for_test.py`
на стороне server_service.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex

from src.clients.ssh import SshClient, SshError

logger = logging.getLogger(__name__)

STEP_PAM_FIX = "pam_fix"
STEP_STAND_SETUP = "stand_setup"

PHASE_BEFORE_KERNEL = "before_kernel"
PHASE_AFTER_BOOT = "after_boot"

PAM_FIX_COMMAND = (
    "sed -i '/auth[[:space:]]*required[[:space:]]*pam_lastlog.so[[:space:]]*inactive=/s/^/#/' "
    "/etc/pam.d/common-auth"
)

# Параметр ядра уходит в sed-выражение с разделителем `|` и в двойные
# кавычки `/etc/default/grub` — allow-list, как `_KERNEL_RE` у пакетов.
_CMDLINE_PARAM_RE = re.compile(r"^[A-Za-z0-9._,:=/+-]{1,128}$")
_LOGIN_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

DEFAULT_PROVISIONING = {
    # Легаси `socket_available()` (`backup_image.py:554-600`).
    "allowed_failed_units": ["astra-mount-lock.service"],
    "degraded_reboot_attempts": 3,
    "disable_pam_lastlog_inactive": True,
    "boot_wait_timeout_seconds": None,
}

_OUTPUT_TAIL = 1500


def parse_provisioning(raw: dict | None) -> dict:
    """Профиль подготовки из payload; нет — легаси-значения."""
    result = dict(DEFAULT_PROVISIONING)
    if isinstance(raw, dict):
        for key in result:
            if key in raw and raw[key] is not None:
                result[key] = raw[key]
    result["allowed_failed_units"] = [str(u) for u in result["allowed_failed_units"] or []]
    result["degraded_reboot_attempts"] = max(0, int(result["degraded_reboot_attempts"] or 0))
    return result


def parse_stand_setup(raw: dict | None) -> dict | None:
    """Шаг настройки стенда из payload; пустой (нет ни параметров, ни скрипта) — `None`."""
    if not isinstance(raw, dict):
        return None
    params = [str(p) for p in raw.get("kernel_cmdline_extra") or []]
    script = raw.get("script") or ""
    if not params and not script.strip():
        return None
    reboot_after = raw.get("reboot_after")
    return {
        "kernel_cmdline_extra": params,
        "script": script,
        "script_is_sensitive": bool(raw.get("script_is_sensitive")),
        "run_as": raw.get("run_as") or "root",
        "phase": raw.get("phase") or PHASE_AFTER_BOOT,
        "reboot_after": True if reboot_after is None else bool(reboot_after),
        "timeout_seconds": int(raw.get("timeout_seconds") or 1800),
    }


async def apply_pam_fix(ssh: SshClient, host: str) -> None:
    rc, _stdout, stderr = await ssh.run(PAM_FIX_COMMAND, sudo=True)
    if rc != 0:
        raise SshError(
            error_code="PREPARE_FOR_TEST_PAM_FIX_FAILED",
            host=host,
            cmd_sanitized="sed -i pam_lastlog /etc/pam.d/common-auth",
            returncode=rc,
            stderr=(stderr or "").strip(),
            message="failed to comment out pam_lastlog inactive= in common-auth",
        )


def validate_cmdline_params(params: list[str], host: str) -> list[str]:
    for param in params:
        if not _CMDLINE_PARAM_RE.fullmatch(param):
            raise SshError(
                error_code="PREPARE_FOR_TEST_INVALID_CMDLINE",
                host=host,
                message=f"kernel parameter {param!r} contains disallowed characters",
            )
    return params


def merge_cmdline(current: str, extra: list[str]) -> str | None:
    """Новая строка параметров или `None`, если все `extra` уже есть."""
    present = current.split()
    missing = [p for p in extra if p not in present]
    if not missing:
        return None
    return " ".join([*present, *missing])


async def add_kernel_cmdline_params(ssh: SshClient, params: list[str], host: str) -> bool:
    """Дописать параметры в `GRUB_CMDLINE_LINUX_DEFAULT`, не дублируя.

    `update-grub` здесь не зовётся — он общий с `kernel_change` (или у
    `stand_setup` без смены ядра зовётся отдельно). Возвращает, изменился
    ли файл.
    """
    if not params:
        return False
    validate_cmdline_params(params, host)
    rc, stdout, stderr = await ssh.run("grep '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub", sudo=True)
    line = (stdout or "").strip().splitlines()
    current = ""
    if rc == 0 and line:
        value = line[-1].split("=", 1)[1].strip()
        current = value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value
    merged = merge_cmdline(current, params)
    if merged is None:
        return False
    validate_cmdline_params(merged.split(), host)
    new_line = f'GRUB_CMDLINE_LINUX_DEFAULT="{merged}"'
    if rc == 0 and line:
        command = f"sed -i 's|^GRUB_CMDLINE_LINUX_DEFAULT=.*|{new_line}|' /etc/default/grub"
    else:
        command = f"sh -c 'echo {shlex.quote(new_line)} >> /etc/default/grub'"
    rc, _stdout, stderr = await ssh.run(command, sudo=True)
    if rc != 0:
        raise SshError(
            error_code="PREPARE_FOR_TEST_GRUB_WRITE_FAILED",
            host=host,
            cmd_sanitized="sed -i GRUB_CMDLINE_LINUX_DEFAULT /etc/default/grub",
            returncode=rc,
            stderr=(stderr or "").strip(),
            message="failed to update GRUB_CMDLINE_LINUX_DEFAULT",
        )
    return True


async def update_grub(ssh: SshClient, host: str) -> None:
    rc, _stdout, stderr = await ssh.run("update-grub", sudo=True)
    if rc != 0:
        raise SshError(
            error_code="PREPARE_FOR_TEST_GRUB_WRITE_FAILED",
            host=host,
            cmd_sanitized="update-grub",
            returncode=rc,
            stderr=(stderr or "").strip(),
            message="update-grub failed",
        )


async def run_setup_script(ssh: SshClient, setup: dict, test_username: str, host: str) -> str:
    """Выполнить скрипт настройки; ненулевой код — `SshError` с хвостом вывода.

    Возвращает вывод (для лога подготовки; у sensitive-скрипта — пусто).
    """
    script = setup.get("script") or ""
    if not script.strip():
        return ""
    if setup.get("run_as") == "test_user":
        if not _LOGIN_RE.fullmatch(test_username or ""):
            raise SshError(
                error_code="PREPARE_FOR_TEST_STAND_SETUP_FAILED",
                host=host,
                message="stand_setup run_as=test_user but the test user is unknown",
            )
        command = f"sudo -u {test_username} -H bash -s"
    else:
        command = "bash -s"
    try:
        rc, stdout, stderr = await asyncio.wait_for(
            ssh.run(command, sudo=True, stdin_payload=script),
            timeout=setup.get("timeout_seconds") or 1800,
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise SshError(
            error_code="PREPARE_FOR_TEST_STAND_SETUP_TIMEOUT",
            host=host,
            message=f"stand_setup script did not finish in {setup.get('timeout_seconds')} s",
        ) from exc
    output = ((stdout or "") + (stderr or "")).strip()
    sensitive = bool(setup.get("script_is_sensitive"))
    if rc != 0:
        tail = "" if sensitive else output[-_OUTPUT_TAIL:]
        raise SshError(
            error_code="PREPARE_FOR_TEST_STAND_SETUP_FAILED",
            host=host,
            cmd_sanitized="bash -s (stand_setup script)",
            returncode=rc,
            stderr=tail,
            message=f"stand_setup script exited with {rc}" + (f": {tail}" if tail else ""),
        )
    if not sensitive:
        logger.info("stand_setup script output on host=%s:\n%s", host, output[-_OUTPUT_TAIL:])
    return "" if sensitive else output


async def failed_units(ssh: SshClient) -> list[str]:
    """Упавшие юниты (`systemctl --state=failed`), как легаси — первым полем."""
    _rc, stdout, _stderr = await ssh.run(
        "systemctl --state=failed --no-legend --plain | awk '{print $1}'",
    )
    return [u.strip() for u in (stdout or "").splitlines() if u.strip()]
