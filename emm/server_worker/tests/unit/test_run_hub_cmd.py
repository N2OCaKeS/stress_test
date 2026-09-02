"""Unit-тесты `src/tasks/_vms_helpers.py::run_hub_cmd` (system-режим).

После перехода VM-подсистемы на `qemu:///system` все команды идут под root.
Разница между libvirt/VM-операцией и инфра-шагом prepare — только в env-
префиксе: libvirt-команда несёт `env LC_ALL=C LIBGUESTFS_BACKEND=direct`, чтобы
переменные дошли до virsh/libguestfs сквозь `sudo -S -p '' <cmd>`; инфра-шаг
идёт как есть. Оба вызывают `ssh.run(..., sudo=True)`.
"""

from __future__ import annotations

import pytest

from src.clients.ssh import SshError
from src.tasks._vms_helpers import LIBVIRT_SESSION_ENV, run_hub_cmd


class _FakeSsh:
    """Мок SshClient: пишет (command, sudo), отдаёт заданный код."""

    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = ""):
        self._resp = (rc, stdout, stderr)
        self.commands: list[str] = []
        self.sudos: list[bool] = []

    async def run(self, command, *, sudo=False, stdin_payload=None):
        self.commands.append(command)
        self.sudos.append(sudo)
        return self._resp


def test_env_constant_is_system_mode():
    # URI qemu:///session ушёл (у root дефолт — system); остались только
    # env-префикс, локаль и backend libguestfs.
    assert LIBVIRT_SESSION_ENV == "env LC_ALL=C LIBGUESTFS_BACKEND=direct"
    assert "qemu:///session" not in LIBVIRT_SESSION_ENV
    assert "LIBVIRT_DEFAULT_URI" not in LIBVIRT_SESSION_ENV


@pytest.mark.asyncio
async def test_libvirt_cmd_gets_env_prefix_and_sudo():
    ssh = _FakeSsh()
    rc, _out, _err = await run_hub_cmd(
        ssh, "virsh domstate station-a", "10.0.0.7", "VM_X", "msg",
    )
    assert rc == 0
    # env-префикс приклеен, команда virsh идёт под sudo (root → qemu:///system).
    assert ssh.commands == ["env LC_ALL=C LIBGUESTFS_BACKEND=direct virsh domstate station-a"]
    assert ssh.sudos == [True]


@pytest.mark.asyncio
async def test_infra_cmd_no_env_prefix_but_sudo():
    ssh = _FakeSsh()
    await run_hub_cmd(
        ssh, "apt-get install -y astra-kvm", "10.0.0.7", "VM_X", "msg", sudo=True,
    )
    # инфра-шаг: тот же root, но без env-префикса.
    assert ssh.commands == ["apt-get install -y astra-kvm"]
    assert not ssh.commands[0].startswith(LIBVIRT_SESSION_ENV)
    assert ssh.sudos == [True]


@pytest.mark.asyncio
async def test_unexpected_code_raises_ssherror():
    ssh = _FakeSsh(rc=1, stderr="boom")
    with pytest.raises(SshError) as exc:
        await run_hub_cmd(ssh, "virsh start station-a", "10.0.0.7", "VM_X", "msg")
    assert exc.value.error_code == "VM_X"
    # cmd_sanitized несёт исходную команду без env-префикса.
    assert "env LC_ALL=C" not in (exc.value.cmd_sanitized or "")


@pytest.mark.asyncio
async def test_ok_codes_pass_through():
    ssh = _FakeSsh(rc=1)
    rc, _out, _err = await run_hub_cmd(
        ssh, "virsh destroy station-a", "10.0.0.7", "VM_X", "msg", ok=(0, 1),
    )
    assert rc == 1
