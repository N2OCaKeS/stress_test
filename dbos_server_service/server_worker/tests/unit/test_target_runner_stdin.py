"""Unit-тесты stdin-канала примитива `run` у `DirectRunner`/`GuestHopRunner`.

`DirectRunner` должен пробрасывать `stdin` в `SshClient.run(stdin_payload=...)`
(контракт серверного `chpasswd`, где `login:pwd` идёт на stdin процесса).
`GuestHopRunner` без `stdin` обязан ходить прежним VM-путём (одна вложенная
`ssh`-строка, stdin не задаётся), а с `stdin` — подавать payload на stdin
внешней hub-команды.
"""

from __future__ import annotations

import pytest

from src.tasks._target_runner import DirectRunner, GuestHopRunner


class _RecordingSsh:
    """Мок SshClient: записывает каждый вызов run как (command, kwargs)."""

    host = "10.0.0.5"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run(self, command, *, sudo=False, stdin_payload=None):
        self.calls.append((command, {"sudo": sudo, "stdin_payload": stdin_payload}))
        return (0, "", "")


@pytest.mark.asyncio
async def test_direct_runner_forwards_stdin_as_payload():
    ssh = _RecordingSsh()
    await DirectRunner(ssh).run("chpasswd", sudo=True, stdin="ops:secret\n")
    cmd, kwargs = ssh.calls[0]
    assert cmd == "chpasswd"
    assert kwargs == {"sudo": True, "stdin_payload": "ops:secret\n"}


@pytest.mark.asyncio
async def test_direct_runner_defaults_stdin_none():
    ssh = _RecordingSsh()
    await DirectRunner(ssh).run("getent passwd ops")
    _cmd, kwargs = ssh.calls[0]
    assert kwargs == {"sudo": False, "stdin_payload": None}


@pytest.mark.asyncio
async def test_guest_hop_no_stdin_preserves_inline_path():
    # Прежнее VM-поведение: connect() оборачивает команду, stdin не задаётся.
    ssh = _RecordingSsh()

    def _connect(remote_cmd, *, sudo=False):
        prefix = "sudo " if sudo else ""
        return f"ssh guest '{prefix}{remote_cmd}'"

    runner = GuestHopRunner(ssh, _connect, host="hub")
    assert runner.host == "hub"
    await runner.run("id ops", sudo=True)
    cmd, kwargs = ssh.calls[0]
    assert cmd == "ssh guest 'sudo id ops'"
    # Без stdin внешний ssh.run зовётся без payload'а (stdin_payload остаётся
    # дефолтным None — VM-путь байт-в-байт прежний).
    assert kwargs["stdin_payload"] is None


@pytest.mark.asyncio
async def test_guest_hop_with_stdin_feeds_outer_ssh():
    ssh = _RecordingSsh()

    def _connect(remote_cmd, *, sudo=False):
        return f"ssh guest '{remote_cmd}'"

    runner = GuestHopRunner(ssh, _connect, host="hub")
    await runner.run("chpasswd", sudo=True, stdin="ops:secret\n")
    cmd, kwargs = ssh.calls[0]
    assert cmd == "ssh guest 'chpasswd'"
    assert kwargs["stdin_payload"] == "ops:secret\n"
