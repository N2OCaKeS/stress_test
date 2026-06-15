"""Тесты PTY-моста интерактивной SSH-консоли (`services/console_bridge.py`).

Покрытие:
  * `_emit_command_audit` пишет `ssh_console.command` в audit-outbox
    (redaction секрета, WARNING на ненулевом exit);
  * `_accumulate_and_audit` разбивает ввод по Enter и эмитит по событию
    на строку (мок asyncssh-канала не нужен — проверяем накопитель);
  * полный PTY-loop: ввод из `console:in` пишется в process.stdin, вывод
    process.stdout публикуется в `console:out`, watchdog/teardown закрывают
    SSH-канал;
  * control-listener: `start` поднимает сессию, `stop` её гасит.

SSH и Redis — моки (без живого соединения). Аудит проверяем по строкам
в `audit_outbox` (реальная БД worker'а), как остальные task-тесты.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from sqlalchemy import select

from src.db.session import AsyncSessionLocal
from src.models.audit_outbox import AuditOutbox
from src.services import console_bridge


async def _fetch_console_audit_rows() -> list[dict]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(AuditOutbox))).scalars().all()
        return [r.payload for r in rows if r.payload.get("action") == "ssh_console.command"]


# ── Мок asyncssh PTY-process ──────────────────────────────────────────────


class _FakeStdin:
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, data):
        self.written.append(data)


class _FakeStdout:
    """Отдаёт заранее заданные чанки, потом EOF (b"")."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProcess:
    def __init__(self, out_chunks):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(out_chunks)
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class _FakeSsh:
    def __init__(self, process):
        self._process = process
        self.connected = False
        self.closed = False

    async def connect(self):
        self.connected = True

    async def open_pty(self, **kwargs):
        return self._process

    async def close(self):
        self.closed = True


class _FakePubSub:
    def __init__(self, messages):
        self._messages = list(messages)
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, *channels):
        self.subscribed.extend(channels)

    async def psubscribe(self, *patterns):
        self.subscribed.extend(patterns)

    async def unsubscribe(self, *channels):
        self.unsubscribed.extend(channels)

    async def aclose(self):
        self.closed = True

    async def listen(self):
        for m in self._messages:
            yield m
        while True:
            await asyncio.sleep(3600)


class _FakeRedis:
    def __init__(self, pubsub=None):
        self._pubsub = pubsub or _FakePubSub([])
        self.published: list[tuple[str, str]] = []

    def pubsub(self):
        return self._pubsub

    async def publish(self, channel, message):
        self.published.append((channel, message))


# ── Audit ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_command_audit_writes_outbox_row():
    await console_bridge._emit_command_audit(
        command="ls -la",
        session_id="csn_1",
        server_id="srv_1",
        department_id="dep_a",
        actor_id="usr_1",
        exit_status=None,
    )
    rows = await _fetch_console_audit_rows()
    assert len(rows) == 1
    p = rows[0]
    assert p["action"] == "ssh_console.command"
    assert p["status"] == "success"
    assert p["target_id"] == "srv_1"
    assert p["target_type"] == "server"
    assert p["actor_id"] == "usr_1"
    assert p["department_id"] == "dep_a"
    assert p["details"]["command"] == "ls -la"
    assert p["details"]["session_id"] == "csn_1"


@pytest.mark.asyncio
async def test_emit_command_audit_nonzero_exit_is_failure():
    await console_bridge._emit_command_audit(
        command="false", session_id="csn_2", server_id="srv_2",
        department_id=None, actor_id=None, exit_status=1,
    )
    rows = await _fetch_console_audit_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "failure"
    assert rows[0]["details"]["exit_status"] == 1


@pytest.mark.asyncio
async def test_emit_command_audit_redacts_secret():
    await console_bridge._emit_command_audit(
        command="curl -H 'Authorization: Bearer sk_supersecrettoken' https://x",
        session_id="csn_3", server_id="srv_3",
        department_id=None, actor_id=None, exit_status=None,
    )
    rows = await _fetch_console_audit_rows()
    assert len(rows) == 1
    # redact_error_message маскирует Bearer-токен — plaintext не оседает.
    assert "sk_supersecrettoken" not in rows[0]["details"]["command"]


@pytest.mark.asyncio
async def test_accumulate_splits_lines_and_audits():
    session = console_bridge._ConsoleSession(
        session_id="csn_lines", host="h", port=22, management_user="dbos",
        key_path="/tmp/k", server_id="srv_lines",
    )
    # Две команды через Enter + хвост без Enter (не должен заэмититься).
    session._accumulate_and_audit(b"whoami\nuptime\npartial")
    # Дать fire-and-forget audit-task'ам отработать.
    await asyncio.sleep(0.1)
    # Прогоняем event loop ещё раз — enqueue идёт через отдельную сессию.
    await asyncio.sleep(0.1)
    rows = await _fetch_console_audit_rows()
    cmds = sorted(r["details"]["command"] for r in rows)
    assert "whoami" in cmds
    assert "uptime" in cmds
    assert "partial" not in cmds  # ещё в буфере, Enter не пришёл


# ── PTY-loop ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_pumps_input_to_pty_and_output_to_redis(monkeypatch):
    process = _FakeProcess(out_chunks=[b"login banner\n"])

    # stdout отдаёт баннер, потом «висит» — иначе output-помпа мгновенно
    # упёрлась бы в EOF и закрыла сессию до того, как input-помпа успеет
    # записать команду в PTY (обе задачи стартуют параллельно и output без
    # await'а выигрывает гонку). С блокировкой после баннера сессию штатно
    # гасит watchdog по idle_timeout, как и задумано тестом.
    class _BannerThenBlockStdout:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        async def read(self, n):
            if self._chunks:
                return self._chunks.pop(0)
            await asyncio.sleep(3600)

    process.stdout = _BannerThenBlockStdout([b"login banner\n"])
    fake_ssh = _FakeSsh(process)

    # Подменяем SshClient на фабрику, отдающую наш фейк.
    def _ssh_factory(**kwargs):
        return fake_ssh

    monkeypatch.setattr(console_bridge, "SshClient", _ssh_factory)

    # in-канал отдаёт одну команду, потом «висит» — сессию закроет watchdog
    # либо stop. Ускоряем idle-таймаут.
    in_msg = {"type": "message", "data": json.dumps({"data": base64.b64encode(b"echo hi\n").decode()})}
    in_pubsub = _FakePubSub([in_msg])
    redis = _FakeRedis(in_pubsub)
    monkeypatch.setattr(console_bridge.redis_pool, "get_redis", lambda: redis)
    monkeypatch.setattr(console_bridge.redis_pool, "get_pubsub_redis", lambda: redis)

    session = console_bridge._ConsoleSession(
        session_id="csn_pump", host="h", port=22, management_user="dbos",
        key_path="/tmp/k", server_id="srv_pump",
        idle_timeout=0.5, max_lifetime=5.0,
    )

    reason = await asyncio.wait_for(session.run(), timeout=5.0)

    # SSH открыт и закрыт (teardown гарантированно закрыл канал).
    assert fake_ssh.connected is True
    assert fake_ssh.closed is True
    assert process.closed is True
    # Ввод доехал до PTY stdin.
    assert b"echo hi\n" in b"".join(process.stdin.written)
    # Вывод PTY опубликован в out-канал (base64-frame).
    out_ch = console_bridge.out_channel("csn_pump")
    out_msgs = [m for ch, m in redis.published if ch == out_ch]
    assert out_msgs
    assert base64.b64decode(json.loads(out_msgs[0])["data"]) == b"login banner\n"
    # ready + closed опубликованы в control-канал.
    ctl_ch = console_bridge.ctl_channel("csn_pump")
    ctl_events = [json.loads(m).get("event") for ch, m in redis.published if ch == ctl_ch]
    assert "ready" in ctl_events
    assert "closed" in ctl_events
    # Команда из ввода заэмичена в аудит.
    rows = await _fetch_console_audit_rows()
    assert any(r["details"]["command"] == "echo hi" for r in rows)
    assert reason in ("pty_eof", "idle_timeout", "client_stop")


@pytest.mark.asyncio
async def test_session_idle_timeout_closes(monkeypatch):
    # PTY без вывода (stdout сразу EOF после ожидания) — здесь stdout висит,
    # чтобы выход дал именно watchdog по idle.
    class _BlockingStdout:
        async def read(self, n):
            await asyncio.sleep(3600)

    process = _FakeProcess(out_chunks=[])
    process.stdout = _BlockingStdout()
    fake_ssh = _FakeSsh(process)
    monkeypatch.setattr(console_bridge, "SshClient", lambda **k: fake_ssh)
    # in-канал ничего не присылает — висит.
    redis = _FakeRedis(_FakePubSub([]))
    monkeypatch.setattr(console_bridge.redis_pool, "get_redis", lambda: redis)
    monkeypatch.setattr(console_bridge.redis_pool, "get_pubsub_redis", lambda: redis)

    session = console_bridge._ConsoleSession(
        session_id="csn_idle", host="h", port=22, management_user="dbos",
        key_path="/tmp/k", idle_timeout=0.3, max_lifetime=5.0,
    )
    reason = await asyncio.wait_for(session.run(), timeout=5.0)
    assert reason == "idle_timeout"
    assert fake_ssh.closed is True


@pytest.mark.asyncio
async def test_session_ssh_error_publishes_error_event(monkeypatch):
    from src.clients.ssh import SshError

    class _FailingSsh:
        async def connect(self):
            raise SshError(error_code="SSH_AUTH_FAILED", host="h")

        async def close(self):
            pass

    monkeypatch.setattr(console_bridge, "SshClient", lambda **k: _FailingSsh())
    redis = _FakeRedis(_FakePubSub([]))
    monkeypatch.setattr(console_bridge.redis_pool, "get_redis", lambda: redis)
    monkeypatch.setattr(console_bridge.redis_pool, "get_pubsub_redis", lambda: redis)

    session = console_bridge._ConsoleSession(
        session_id="csn_err", host="h", port=22, management_user="dbos",
        key_path="/tmp/k",
    )
    reason = await asyncio.wait_for(session.run(), timeout=5.0)
    assert reason == "error"
    ctl_ch = console_bridge.ctl_channel("csn_err")
    events = [json.loads(m) for ch, m in redis.published if ch == ctl_ch]
    assert any(e.get("event") == "error" and e.get("error_code") == "SSH_AUTH_FAILED" for e in events)


# ── Control listener ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_ctl_start_spawns_session(monkeypatch):
    spawned: dict = {}

    def fake_build(session_id, msg):
        spawned["session_id"] = session_id
        spawned["msg"] = msg

        class _S:
            def __init__(self):
                self.stopped = False

            async def run(self):
                # Короткая «сессия» — сразу финиширует.
                return "client_stop"

            def signal_stop(self):
                self.stopped = True

        s = _S()
        spawned["session"] = s
        return s

    monkeypatch.setattr(console_bridge, "_build_session_from_start", fake_build)
    console_bridge._ACTIVE_SESSIONS.clear()

    msg = {
        "type": "pmessage",
        "channel": console_bridge.ctl_channel("csn_ctl"),
        "data": json.dumps({"action": "start", "host": "h", "server_id": "srv"}),
    }
    await console_bridge._handle_ctl_message(msg)
    # Дать спавн-task стартануть.
    await asyncio.sleep(0.05)
    assert spawned["session_id"] == "csn_ctl"
    assert spawned["msg"]["host"] == "h"
    # cleanup
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_handle_ctl_stop_signals_session(monkeypatch):
    class _S:
        def __init__(self):
            self.stopped = False

        def signal_stop(self):
            self.stopped = True

    s = _S()
    console_bridge._ACTIVE_SESSIONS.clear()

    async def _never():
        await asyncio.sleep(3600)

    t = asyncio.create_task(_never())
    console_bridge._ACTIVE_SESSIONS["csn_stop"] = (t, s)
    try:
        msg = {
            "type": "pmessage",
            "channel": console_bridge.ctl_channel("csn_stop"),
            "data": json.dumps({"action": "stop"}),
        }
        await console_bridge._handle_ctl_message(msg)
        assert s.stopped is True
    finally:
        t.cancel()
        console_bridge._ACTIVE_SESSIONS.clear()


@pytest.mark.asyncio
async def test_handle_ctl_rejects_bad_session_id(monkeypatch):
    called = {"build": False}

    def fake_build(session_id, msg):
        called["build"] = True

    monkeypatch.setattr(console_bridge, "_build_session_from_start", fake_build)
    # Канал с wildcard-символом в session_id — должен быть отвергнут.
    msg = {
        "type": "pmessage",
        "channel": "console:ctl:bad*id",
        "data": json.dumps({"action": "start"}),
    }
    await console_bridge._handle_ctl_message(msg)
    assert called["build"] is False
