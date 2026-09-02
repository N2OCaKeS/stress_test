"""Тесты WebSocket-эндпоинта интерактивной консоли ВМ (`vm_console_ws`).

Зеркало серверной консоли (`test_console_ws.py`), но цель — ВМ. Покрытие:
  * нет токена → close 4401;
  * нет `account_id` → close 4400;
  * невалидный `kind` (vnc/spice — это графический прокси) → close 4400;
  * нет права на учётку → close 4403 + denied-аудит (target_type=vm);
  * не найдено / не привязано → close 4404;
  * ВМ занята другим / у учётки нет пароля → close 4409;
  * happy-path: креды учётки стэшатся, start несёт `target_type=vm`,
    `console_kind`, домен ВМ и адрес hub'а; аудит session_open/close с
    target_type=vm и account_id/login/kind.

WS не гоняем через реальный handshake — вызываем хендлер с фейковым
WebSocket'ом и подменяем auth/резолв кред/Redis на уровне модуля `console`.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from src.api.v1.endpoints import console
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from starlette.websockets import WebSocketState


# ── Фейки ─────────────────────────────────────────────────────────────────


class FakeWebSocket:
    def __init__(self, *, headers=None, subprotocols=None, incoming=None, query_params=None):
        self.headers = headers or {}
        self.scope = {"subprotocols": subprotocols or []}
        self.query_params = {"account_id": "acc_1"} if query_params is None else query_params
        self.application_state = WebSocketState.CONNECTING
        self.client_state = WebSocketState.CONNECTING
        self.accepted = False
        self.accepted_subprotocol = None
        self.closed_code = None
        self.closed_reason = None
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []
        self._incoming = list(incoming or [])

    async def accept(self, subprotocol=None):
        self.accepted = True
        self.accepted_subprotocol = subprotocol
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED

    async def close(self, code=1000, reason=None):
        self.closed_code = code
        self.closed_reason = reason
        self.application_state = WebSocketState.DISCONNECTED

    async def send_bytes(self, data: bytes):
        self.sent_bytes.append(data)

    async def send_text(self, text: str):
        self.sent_text.append(text)

    async def receive(self):
        if self._incoming:
            return self._incoming.pop(0)
        return {"type": "websocket.disconnect"}


class FakePubSub:
    def __init__(self, messages):
        self._messages = list(messages)
        self.subscribed: list[str] = []
        self.closed = False

    async def subscribe(self, *channels):
        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels):
        pass

    async def aclose(self):
        self.closed = True

    async def listen(self):
        for m in self._messages:
            yield m
        while True:
            await asyncio.sleep(3600)


class FakeRedis:
    def __init__(self, pubsub):
        self._pubsub = pubsub
        self.published: list[tuple[str, str]] = []

    def pubsub(self):
        return self._pubsub

    async def publish(self, channel, message):
        self.published.append((channel, message))

    async def aclose(self):
        pass


def _make_vm():
    return SimpleNamespace(
        id="vm_1", name="vm-astra-01",
        ip_address=None, department_id="dep_a",
    )


def _make_hub():
    return SimpleNamespace(
        id="srv_hub", ip_address="10.0.0.9", ssh_port=22,
        is_managed=True, management_user="dbos",
    )


def _identity():
    return SimpleNamespace(
        user_id="usr_1", department_id="dep_a", is_banned=False,
        allowed_services=["server_service"],
    )


@pytest.fixture(autouse=True)
def _patch_console(monkeypatch):
    emitted: list[dict] = []

    def fake_emit(action, **kwargs):
        emitted.append({"action": action, **kwargs})

    async def fake_authenticate(token):
        return _identity()

    monkeypatch.setattr(console.audit_service, "emit", fake_emit)
    monkeypatch.setattr(console, "_authenticate", fake_authenticate)

    async def fake_resolve(db, identity, vm_id, account_id):
        return _make_vm(), _make_hub(), {
            "login": "svc", "password": "pw", "ssh_private_key": None,
        }

    async def fake_store(stash_key, creds):
        return None

    async def fake_delete(stash_key):
        return None

    monkeypatch.setattr(console.vm_svc, "resolve_vm_console_credentials", fake_resolve)
    monkeypatch.setattr(console.worker_client, "store_console_creds", fake_store)
    monkeypatch.setattr(console.worker_client, "delete_console_creds", fake_delete)

    class _FakeSessionCM:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(console, "AsyncSessionLocal", lambda: _FakeSessionCM())
    return emitted


# ── Gate ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_token_closes_4401(_patch_console):
    ws = FakeWebSocket(headers={})
    await console.vm_console_ws(ws, "vm_1")
    assert ws.closed_code == console._WS_CLOSE_UNAUTHENTICATED
    assert ws.closed_reason == "ACCESS_TOKEN_MISSING"
    assert not ws.accepted


@pytest.mark.asyncio
async def test_missing_account_id_closes_4400(_patch_console):
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"}, query_params={})
    await console.vm_console_ws(ws, "vm_1")
    assert ws.closed_code == console._WS_CLOSE_BAD_REQUEST
    assert ws.closed_reason == "ACCOUNT_ID_REQUIRED"


@pytest.mark.asyncio
async def test_invalid_kind_closes_4400(_patch_console):
    ws = FakeWebSocket(
        headers={"Authorization": "Bearer tok"},
        query_params={"account_id": "acc_1", "kind": "vnc"},
    )
    await console.vm_console_ws(ws, "vm_1")
    assert ws.closed_code == console._WS_CLOSE_BAD_REQUEST
    assert ws.closed_reason == "INVALID_CONSOLE_KIND"


@pytest.mark.asyncio
async def test_permission_denied_closes_4403(monkeypatch, _patch_console):
    async def resolve_denied(*a, **k):
        raise AuthorizationError(error_code="PERMISSION_DENIED", message="no")

    monkeypatch.setattr(console.vm_svc, "resolve_vm_console_credentials", resolve_denied)
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.vm_console_ws(ws, "vm_1")
    assert ws.closed_code == console._WS_CLOSE_FORBIDDEN
    assert ws.closed_reason == "PERMISSION_DENIED"
    assert not ws.accepted
    assert any(
        e["action"] == "ssh_console.session_open"
        and e["status"] == "denied"
        and e.get("target_type") == "vm"
        for e in _patch_console
    )


@pytest.mark.asyncio
async def test_not_linked_closes_4404(monkeypatch, _patch_console):
    async def resolve_nf(*a, **k):
        raise NotFoundError(error_code="ACCOUNT_NOT_LINKED", message="nope")

    monkeypatch.setattr(console.vm_svc, "resolve_vm_console_credentials", resolve_nf)
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.vm_console_ws(ws, "vm_1")
    assert ws.closed_code == console._WS_CLOSE_NOT_FOUND
    assert ws.closed_reason == "ACCOUNT_NOT_LINKED"


@pytest.mark.asyncio
async def test_vm_reserved_closes_4409(monkeypatch, _patch_console):
    async def resolve_reserved(*a, **k):
        raise ConflictError(error_code="VM_RESERVED", message="busy")

    monkeypatch.setattr(console.vm_svc, "resolve_vm_console_credentials", resolve_reserved)
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.vm_console_ws(ws, "vm_1")
    assert ws.closed_code == console._WS_CLOSE_CONFLICT
    assert ws.closed_reason == "VM_RESERVED"


# ── Happy-path bridge ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vm_bridge_publishes_vm_start(monkeypatch, _patch_console):
    monkeypatch.setattr(console, "console_session_id", lambda: "csn_vm")
    ctl_ch = console.worker_client.console_ctl_channel("csn_vm")
    out_ch = console.worker_client.console_out_channel("csn_vm")
    in_ch = console.worker_client.console_in_channel("csn_vm")

    out_payload = json.dumps({"data": base64.b64encode(b"guest$ ").decode()})
    messages = [
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "ready"})},
        {"type": "message", "channel": out_ch, "data": out_payload},
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "closed", "reason": "pty_eof"})},
    ]
    pubsub = FakePubSub(messages)
    redis = FakeRedis(pubsub)
    monkeypatch.setattr(console.worker_client, "get_worker_redis", lambda: redis)
    monkeypatch.setattr(console.worker_client, "_creds_redis_client", redis)

    incoming = [
        {"type": "websocket.receive", "text": "whoami\n"},
        {"type": "websocket.disconnect"},
    ]
    ws = FakeWebSocket(
        headers={"Authorization": "Bearer tok"},
        subprotocols=["console.v1", "bearer.tok"],
        incoming=incoming,
    )

    await asyncio.wait_for(console.vm_console_ws(ws, "vm_1"), timeout=5.0)

    assert ws.accepted
    assert ws.accepted_subprotocol == "console.v1"
    start = [m for ch, m in redis.published if ch == ctl_ch and '"start"' in m]
    assert start, "start control message not published"
    obj = json.loads(start[0])
    assert obj["target_type"] == "vm"
    assert obj["console_kind"] == "ssh"
    assert obj["vm_id"] == "vm_1"
    assert obj["vm_domain"] == "vm-astra-01"
    # SSH-таргет воркера — адрес hub'а (по IP), не гостя/hostname.
    assert obj["host"] == "10.0.0.9"
    assert obj["hub_server_id"] == "srv_hub"
    assert obj["creds_stash_key"].startswith("dbos:console_creds:")
    assert "password" not in obj
    # ввод клиента ушёл в in-канал.
    in_msgs = [m for ch, m in redis.published if ch == in_ch]
    assert in_msgs
    assert base64.b64decode(json.loads(in_msgs[0])["data"]) == b"whoami\n"
    # вывод доставлен клиенту.
    assert b"guest$ " in b"".join(ws.sent_bytes)
    # stop опубликован на disconnect.
    assert any(ch == ctl_ch and '"stop"' in m for ch, m in redis.published)
    # session_open + session_close с target_type=vm.
    opened = [e for e in _patch_console if e["action"] == "ssh_console.session_open"][0]
    closed = [e for e in _patch_console if e["action"] == "ssh_console.session_close"][0]
    assert opened["target_type"] == "vm"
    assert opened["target_id"] == "vm_1"
    assert opened["details"]["account_id"] == "acc_1"
    assert opened["details"]["login"] == "svc"
    assert opened["details"]["kind"] == "ssh"
    assert closed["target_type"] == "vm"


@pytest.mark.asyncio
async def test_vm_serial_kind_flows_to_start(monkeypatch, _patch_console):
    monkeypatch.setattr(console, "console_session_id", lambda: "csn_ser")
    ctl_ch = console.worker_client.console_ctl_channel("csn_ser")
    messages = [
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "ready"})},
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "closed", "reason": "pty_eof"})},
    ]
    pubsub = FakePubSub(messages)
    redis = FakeRedis(pubsub)
    monkeypatch.setattr(console.worker_client, "get_worker_redis", lambda: redis)
    monkeypatch.setattr(console.worker_client, "_creds_redis_client", redis)

    ws = FakeWebSocket(
        headers={"Authorization": "Bearer tok"},
        query_params={"account_id": "acc_1", "kind": "serial"},
    )
    await asyncio.wait_for(console.vm_console_ws(ws, "vm_1"), timeout=5.0)

    start = [m for ch, m in redis.published if ch == ctl_ch and '"start"' in m]
    assert start
    assert json.loads(start[0])["console_kind"] == "serial"
