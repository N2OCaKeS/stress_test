"""Тесты VM-ветки PTY-моста консоли (`services/console_bridge.py`).

Консоль ВМ идёт не напрямую к боксу, а через hub в гостя:
  * `kind=ssh` — SSH к hub'у под управляющей учёткой, оттуда вложенный
    `sshpass ... ssh <login>@<guest_ip>` под выбранным аккаунтом;
  * `kind=serial` — `virsh console <domain>` на самом hub'е.

Покрытие: сборка VM-сессии из start-сообщения (ssh требует creds_stash_key,
serial — нет, кривой kind отбивается), построение команд hub→гость,
валидация домена/логина/IP, полный ssh-run (open_hub_session + _guest_ip +
open_pty с нужной командой) и VM-таргет в аудите команд.

SSH/Redis/hub-сессия — моки. `open_hub_session`/`_guest_ip` подменяются в
их исходных модулях (ленивый импорт в `_open_vm_process` читает актуальный
атрибут).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

import src.tasks._vms_helpers as vms_helpers
import src.tasks.vms as vms_tasks
from src.clients.ssh import SshError
from src.db.session import AsyncSessionLocal
from src.models.audit_outbox import AuditOutbox
from src.services import console_bridge


# ── Фейки ─────────────────────────────────────────────────────────────────


class _BlockingStdout:
    async def read(self, n):
        await asyncio.sleep(3600)


class _FakeProcess:
    def __init__(self):
        self.stdin = _Stdin()
        self.stdout = _BlockingStdout()
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class _Stdin:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)


class _FakeHubSsh:
    """Сессия к hub'у: capture команды open_pty, отдаёт фейковый process."""

    def __init__(self, process):
        self._process = process
        self.pty_command = None
        self.closed = False

    async def open_pty(self, *, command=None, **kwargs):
        self.pty_command = command
        return self._process

    async def close(self):
        self.closed = True


class _FakePubSub:
    def __init__(self):
        self.closed = False

    async def subscribe(self, *channels):
        pass

    async def unsubscribe(self, *channels):
        pass

    async def aclose(self):
        self.closed = True

    async def listen(self):
        # Async-generator, который «висит» — in-помпа не должна ни закончиться
        # (тогда pty_eof), ни падать. Сессию гасит watchdog по idle_timeout.
        while True:
            await asyncio.sleep(3600)
            yield None  # недостижимо; делает функцию генератором


class _FakeRedis:
    def __init__(self):
        self.published = []

    def pubsub(self):
        return _FakePubSub()

    async def publish(self, channel, message):
        self.published.append((channel, message))


def _hub_msg(**over):
    msg = {
        "action": "start",
        "target_type": "vm",
        "console_kind": "ssh",
        "vm_id": "vm_1",
        "vm_domain": "vm-astra-01",
        "guest_ip": None,
        "creds_stash_key": "dbos:console_creds:ccd_vm",
        "account_id": "acc_1",
        "target_department_id": "dep_a",
        "actor_id": "usr_1",
        "hub_server_id": "srv_hub",
        "server_id": "srv_hub",
        "host": "10.0.0.9",
        "ssh_port": 22,
        "is_managed": True,
        "management_user": "dbos",
    }
    msg.update(over)
    return msg


# ── Сборка VM-сессии из start ──────────────────────────────────────────────


def test_build_vm_ssh_session_from_start():
    session = console_bridge._build_session_from_start("csn_vm", _hub_msg())
    assert session.is_vm is True
    assert session.console_kind == "ssh"
    assert session.vm_id == "vm_1"
    assert session.vm_domain == "vm-astra-01"
    # Хост сессии — адрес hub'а (SSH-таргет), а не гостя.
    assert session.host == "10.0.0.9"
    assert session.creds_stash_key == "dbos:console_creds:ccd_vm"
    # Весь start уезжает в hub_msg для open_hub_session.
    assert session.hub_msg["management_user"] == "dbos"


def test_build_vm_ssh_requires_creds_stash_key():
    with pytest.raises(SshError) as ei:
        console_bridge._build_session_from_start(
            "csn_vm2", _hub_msg(creds_stash_key=None),
        )
    assert ei.value.error_code == "CONSOLE_CREDS_KEY_MISSING"


def test_build_vm_serial_needs_no_stash():
    session = console_bridge._build_session_from_start(
        "csn_vm3", _hub_msg(console_kind="serial", creds_stash_key=None),
    )
    assert session.is_vm is True
    assert session.console_kind == "serial"
    assert session.creds_stash_key is None


def test_build_vm_rejects_bad_kind():
    with pytest.raises(SshError) as ei:
        console_bridge._build_session_from_start(
            "csn_vm4", _hub_msg(console_kind="telnet"),
        )
    assert ei.value.error_code == "VM_CONSOLE_INVALID_KIND"


# ── Построение команд hub→гость ────────────────────────────────────────────


def test_guest_ssh_command_builds_sshpass_login():
    s = console_bridge._ConsoleSession(
        session_id="csn", host="10.0.0.9", port=22, login="svcuser",
        password="svcpass", is_vm=True, console_kind="ssh", vm_domain="vm-a",
    )
    cmd = s._guest_ssh_command("192.168.122.50")
    assert "sshpass -e ssh" in cmd
    assert "-tt" in cmd
    assert "svcuser@192.168.122.50" in cmd
    # Пароль подан через SSHPASS-env, не аргументом.
    assert "SSHPASS=" in cmd
    assert "-p " not in cmd


def test_serial_command_uses_virsh_console():
    s = console_bridge._ConsoleSession(
        session_id="csn", host="10.0.0.9", port=22, login="", password="",
        is_vm=True, console_kind="serial", vm_domain="vm-astra-01",
    )
    assert s._serial_command() == "sudo virsh console --force vm-astra-01"


def test_guest_ssh_command_rejects_bad_ip():
    s = console_bridge._ConsoleSession(
        session_id="csn", host="10.0.0.9", port=22, login="svcuser",
        password="pw", is_vm=True, console_kind="ssh", vm_domain="vm-a",
    )
    with pytest.raises(SshError) as ei:
        s._guest_ssh_command("not-an-ip; rm -rf /")
    assert ei.value.error_code == "VM_CONSOLE_INVALID_TARGET"


def test_guest_ssh_command_rejects_bad_login():
    s = console_bridge._ConsoleSession(
        session_id="csn", host="10.0.0.9", port=22, login="bad login;",
        password="pw", is_vm=True, console_kind="ssh", vm_domain="vm-a",
    )
    with pytest.raises(SshError) as ei:
        s._guest_ssh_command("192.168.122.50")
    assert ei.value.error_code == "VM_CONSOLE_INVALID_TARGET"


def test_serial_command_rejects_bad_domain():
    s = console_bridge._ConsoleSession(
        session_id="csn", host="10.0.0.9", port=22, login="", password="",
        is_vm=True, console_kind="serial", vm_domain="vm; reboot",
    )
    with pytest.raises(SshError) as ei:
        s._serial_command()
    assert ei.value.error_code == "VM_CONSOLE_INVALID_TARGET"


# ── Полный run: ssh через hub в гостя ──────────────────────────────────────


@pytest.mark.asyncio
async def test_vm_ssh_run_opens_pty_via_hub(monkeypatch):
    captured: dict = {}
    hub_ssh = _FakeHubSsh(_FakeProcess())

    async def fake_open_hub(msg):
        captured["hub_msg"] = msg
        return hub_ssh, "10.0.0.9"

    async def fake_guest_ip(ssh, host, name):
        captured["domain"] = name
        assert ssh is hub_ssh
        return "192.168.122.77"

    async def fake_read(stash_key):
        captured["read_key"] = stash_key
        return "svcuser", "svcpass", None

    async def fake_delete(stash_key):
        captured["deleted_key"] = stash_key

    monkeypatch.setattr(vms_helpers, "open_hub_session", fake_open_hub)
    monkeypatch.setattr(vms_tasks, "_guest_ip", fake_guest_ip)
    monkeypatch.setattr(console_bridge, "_read_console_creds", fake_read)
    monkeypatch.setattr(console_bridge, "_delete_console_creds", fake_delete)
    redis = _FakeRedis()
    monkeypatch.setattr(console_bridge.redis_pool, "get_redis", lambda: redis)
    monkeypatch.setattr(console_bridge.redis_pool, "get_pubsub_redis", lambda: redis)

    session = console_bridge._build_session_from_start("csn_vmrun", _hub_msg())
    session.idle_timeout = 0.3
    session.max_lifetime = 5.0
    reason = await asyncio.wait_for(session.run(), timeout=5.0)

    # open_hub_session получил весь start (адресация hub'а).
    assert captured["hub_msg"]["host"] == "10.0.0.9"
    # Гость резолвился через hub-сессию по домену ВМ.
    assert captured["domain"] == "vm-astra-01"
    # PTY поднят командой вложенного ssh в гостя под аккаунтом.
    assert "svcuser@192.168.122.77" in hub_ssh.pty_command
    assert "sshpass -e ssh" in hub_ssh.pty_command
    # Креды аккаунта прочитаны из stash и удалены.
    assert captured["read_key"] == "dbos:console_creds:ccd_vm"
    assert captured["deleted_key"] == "dbos:console_creds:ccd_vm"
    # Hub-сессия закрыта teardown'ом.
    assert hub_ssh.closed is True
    assert reason == "idle_timeout"
    # ready опубликован в control.
    ctl_ch = console_bridge.ctl_channel("csn_vmrun")
    events = [json.loads(m).get("event") for ch, m in redis.published if ch == ctl_ch]
    assert "ready" in events


@pytest.mark.asyncio
async def test_vm_ssh_run_uses_static_guest_ip_hint(monkeypatch):
    """Bridge-ВМ со статическим IP: _guest_ip не дёргаем, берём hint из start."""
    hub_ssh = _FakeHubSsh(_FakeProcess())
    called = {"guest_ip": False}

    async def fake_open_hub(msg):
        return hub_ssh, "10.0.0.9"

    async def fake_guest_ip(ssh, host, name):
        called["guest_ip"] = True
        return "0.0.0.0"

    async def fake_read(stash_key):
        return "svcuser", "svcpass", None

    monkeypatch.setattr(vms_helpers, "open_hub_session", fake_open_hub)
    monkeypatch.setattr(vms_tasks, "_guest_ip", fake_guest_ip)
    monkeypatch.setattr(console_bridge, "_read_console_creds", fake_read)
    monkeypatch.setattr(console_bridge, "_delete_console_creds", lambda k: asyncio.sleep(0))
    redis = _FakeRedis()
    monkeypatch.setattr(console_bridge.redis_pool, "get_redis", lambda: redis)
    monkeypatch.setattr(console_bridge.redis_pool, "get_pubsub_redis", lambda: redis)

    session = console_bridge._build_session_from_start(
        "csn_hint", _hub_msg(guest_ip="10.177.5.5"),
    )
    session.idle_timeout = 0.3
    await asyncio.wait_for(session.run(), timeout=5.0)

    assert called["guest_ip"] is False
    assert "svcuser@10.177.5.5" in hub_ssh.pty_command


# ── Полный run: serial через virsh console ─────────────────────────────────


@pytest.mark.asyncio
async def test_vm_serial_run_opens_virsh_console(monkeypatch):
    hub_ssh = _FakeHubSsh(_FakeProcess())
    read_called = {"n": 0}

    async def fake_open_hub(msg):
        return hub_ssh, "10.0.0.9"

    async def fake_read(stash_key):
        read_called["n"] += 1
        return "svcuser", "svcpass", None

    monkeypatch.setattr(vms_helpers, "open_hub_session", fake_open_hub)
    monkeypatch.setattr(console_bridge, "_read_console_creds", fake_read)
    monkeypatch.setattr(console_bridge, "_delete_console_creds", lambda k: asyncio.sleep(0))
    redis = _FakeRedis()
    monkeypatch.setattr(console_bridge.redis_pool, "get_redis", lambda: redis)
    monkeypatch.setattr(console_bridge.redis_pool, "get_pubsub_redis", lambda: redis)

    session = console_bridge._build_session_from_start(
        "csn_serial", _hub_msg(console_kind="serial", creds_stash_key=None),
    )
    session.idle_timeout = 0.3
    reason = await asyncio.wait_for(session.run(), timeout=5.0)

    assert hub_ssh.pty_command == "sudo virsh console --force vm-astra-01"
    # Serial не читает креды аккаунта.
    assert read_called["n"] == 0
    assert reason == "idle_timeout"


# ── Аудит команд VM-таргета ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vm_command_audit_targets_vm():
    await console_bridge._emit_command_audit(
        command="uptime",
        session_id="csn_vmaudit",
        server_id=None,
        department_id="dep_a",
        actor_id="usr_1",
        exit_status=None,
        vm_id="vm_42",
    )
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AuditOutbox))).scalars().all()
        mine = [
            r.payload for r in rows
            if r.payload.get("action") == "ssh_console.command"
            and r.payload.get("details", {}).get("session_id") == "csn_vmaudit"
        ]
    assert len(mine) == 1
    p = mine[0]
    assert p["target_type"] == "vm"
    assert p["target_id"] == "vm_42"
    assert p["details"]["vm_id"] == "vm_42"
