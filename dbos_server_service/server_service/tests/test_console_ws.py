"""Тесты WebSocket-эндпоинта интерактивной SSH-консоли.

Консоль подключается под ВЫБРАННЫМ server_account (`account_id` в query),
prepare НЕ требуется. Покрытие:
  * нет токена → close 4401;
  * нет `account_id` → close 4400;
  * нет права `(server, console)` → close 4403 + denied-аудит;
  * нет права на креды аккаунта (`view_password`) → close 4403;
  * не найден / cross-dept (сервер или аккаунт) → close 4404;
  * у аккаунта нет пароля → close 4409 ACCOUNT_HAS_NO_PASSWORD;
  * decommissioned → close 4409 SERVER_DECOMMISSIONED;
  * managed=False сервер → консоль работает (prepare не нужен);
  * happy-path bridge: креды аккаунта стэшатся в Redis, после `ready` ввод
    клиента публикуется в `console:in:<sid>`, вывод из `console:out:<sid>`
    уходит клиенту, на disconnect публикуется `stop`; start несёт
    `creds_stash_key`, аудит несёт `account_id`/`login`.

WebSocket не гоняем через реальный handshake (конфликт sync-TestClient с
async-DB-фикстурой) — вызываем хендлер с фейковым WebSocket'ом и подменяем
auth/RBAC/visibility/creds/Redis на уровне модуля `console`. Это упражняет
ровно тот же код RBAC/моста.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from src.api.v1.endpoints import console
from src.core.constants import ServerStatus
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from starlette.websockets import WebSocketState


# ── Фейки ─────────────────────────────────────────────────────────────────


class FakeWebSocket:
    """Минимальный async-WebSocket для unit-теста хендлера."""

    def __init__(self, *, headers=None, subprotocols=None, incoming=None, query_params=None):
        self.headers = headers or {}
        self.scope = {"subprotocols": subprotocols or []}
        # `account_id` обязателен — по умолчанию даём валидный.
        self.query_params = {"account_id": "acc_1"} if query_params is None else query_params
        self.application_state = WebSocketState.CONNECTING
        self.client_state = WebSocketState.CONNECTING
        self.accepted = False
        self.accepted_subprotocol = None
        self.closed_code = None
        self.closed_reason = None
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []
        # Очередь входящих сообщений (dict как `websocket.receive`).
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
        # Имитируем disconnect, когда сообщения кончились.
        return {"type": "websocket.disconnect"}


class FakePubSub:
    """Pub/sub-мок: `listen()` отдаёт заранее заданные сообщения, потом висит."""

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
        # После выдачи всех сообщений «зависаем», чтобы помпа не закончилась
        # раньше другой ветки — тест отменит task через FIRST_COMPLETED.
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


def _make_server(*, is_managed=True, status=ServerStatus.ONLINE):
    return SimpleNamespace(
        id="srv_console1",
        hostname="host.example",
        ssh_port=22,
        is_managed=is_managed,
        management_user="dbos",
        department_id="dep_a",
        status=status,
    )


def _identity():
    return SimpleNamespace(
        user_id="usr_1",
        department_id="dep_a",
        is_banned=False,
        allowed_services=["server_service"],
    )


@pytest.fixture(autouse=True)
def _patch_console(monkeypatch):
    """Подменяем auth/RBAC/visibility/Redis в модуле console для unit-теста."""
    emitted: list[dict] = []

    def fake_emit(action, **kwargs):
        emitted.append({"action": action, **kwargs})

    async def fake_authenticate(token):
        return _identity()

    monkeypatch.setattr(console.audit_service, "emit", fake_emit)
    monkeypatch.setattr(console, "_authenticate", fake_authenticate)

    # Дефолтный happy-резолв кред + no-op Redis-stash (отдельные тесты
    # переопределяют под свои сценарии).
    async def fake_resolve(db, identity, account_id, server):
        return {"login": "svc", "password": "pw", "ssh_private_key": None}

    async def fake_store(stash_key, creds):
        return None

    async def fake_delete(stash_key):
        return None

    monkeypatch.setattr(
        console.account_svc, "resolve_bootstrap_credentials", fake_resolve,
    )
    monkeypatch.setattr(console.worker_client, "store_console_creds", fake_store)
    monkeypatch.setattr(console.worker_client, "delete_console_creds", fake_delete)

    # AsyncSessionLocal → async-CM, отдающий заглушку (require_action /
    # load_visible_server мокаются отдельно, db не используется реально).
    class _FakeSessionCM:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(console, "AsyncSessionLocal", lambda: _FakeSessionCM())
    return emitted


# ── RBAC / gate ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_token_closes_4401(monkeypatch, _patch_console):
    ws = FakeWebSocket(headers={})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_UNAUTHENTICATED
    assert ws.closed_reason == "ACCESS_TOKEN_MISSING"
    assert not ws.accepted


@pytest.mark.asyncio
async def test_missing_account_id_closes_4400(monkeypatch, _patch_console):
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"}, query_params={})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_BAD_REQUEST
    assert ws.closed_reason == "ACCOUNT_ID_REQUIRED"
    assert not ws.accepted


@pytest.mark.asyncio
async def test_no_console_permission_closes_4403(monkeypatch, _patch_console):
    async def deny(*a, **k):
        raise AuthorizationError(error_code="PERMISSION_DENIED", message="no")

    monkeypatch.setattr(console.permissions, "require_action", deny)
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_FORBIDDEN
    assert ws.closed_reason == "PERMISSION_DENIED"
    assert not ws.accepted
    assert any(
        e["action"] == "ssh_console.session_open" and e["status"] == "denied"
        for e in _patch_console
    )


@pytest.mark.asyncio
async def test_no_creds_permission_closes_4403(monkeypatch, _patch_console):
    """Право console есть, но `view_password` на аккаунт — нет → 4403."""
    async def allow(*a, **k):
        return None

    async def load(*a, **k):
        return _make_server()

    async def resolve_denied(*a, **k):
        raise AuthorizationError(error_code="PERMISSION_DENIED", message="no creds")

    monkeypatch.setattr(console.permissions, "require_action", allow)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(
        console.account_svc, "resolve_bootstrap_credentials", resolve_denied,
    )
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_FORBIDDEN
    assert ws.closed_reason == "PERMISSION_DENIED"
    assert not ws.accepted
    assert any(
        e["action"] == "ssh_console.session_open"
        and e["status"] == "denied"
        and e.get("details", {}).get("account_id") == "acc_1"
        for e in _patch_console
    )


@pytest.mark.asyncio
async def test_account_not_found_closes_4404(monkeypatch, _patch_console):
    async def allow(*a, **k):
        return None

    async def load(*a, **k):
        return _make_server()

    async def resolve_notfound(*a, **k):
        raise NotFoundError(error_code="ACCOUNT_NOT_LINKED", message="nope")

    monkeypatch.setattr(console.permissions, "require_action", allow)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(
        console.account_svc, "resolve_bootstrap_credentials", resolve_notfound,
    )
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_NOT_FOUND
    assert ws.closed_reason == "ACCOUNT_NOT_LINKED"


@pytest.mark.asyncio
async def test_account_has_no_password_closes_4409(monkeypatch, _patch_console):
    async def allow(*a, **k):
        return None

    async def load(*a, **k):
        return _make_server()

    async def resolve_no_pw(*a, **k):
        raise ConflictError(error_code="ACCOUNT_HAS_NO_PASSWORD", message="no pw")

    monkeypatch.setattr(console.permissions, "require_action", allow)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(
        console.account_svc, "resolve_bootstrap_credentials", resolve_no_pw,
    )
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_CONFLICT
    assert ws.closed_reason == "ACCOUNT_HAS_NO_PASSWORD"


@pytest.mark.asyncio
async def test_decommissioned_closes_conflict(monkeypatch, _patch_console):
    async def allow(*a, **k):
        return None

    async def load(*a, **k):
        return _make_server(status=ServerStatus.DECOMMISSIONED)

    monkeypatch.setattr(console.permissions, "require_action", allow)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_CONFLICT
    assert ws.closed_reason == "SERVER_DECOMMISSIONED"


@pytest.mark.asyncio
async def test_cross_dept_closes_4404(monkeypatch, _patch_console):
    async def allow(*a, **k):
        return None

    async def load(*a, **k):
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="nope")

    monkeypatch.setattr(console.permissions, "require_action", allow)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_NOT_FOUND


# ── Happy-path bridge ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_publishes_input_and_relays_output(monkeypatch, _patch_console):
    async def allow(*a, **k):
        return None

    async def load(*a, **k):
        return _make_server()

    monkeypatch.setattr(console.permissions, "require_action", allow)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)

    # Control: ready; затем out-frame с "hello"; затем ctl closed.
    # Канал в сообщениях должен совпадать с тем, что построит мост — но
    # session_id генерится внутри. Поэтому сообщения помечаем шаблонными
    # каналами и подменяем session_id через фикс генератора.
    monkeypatch.setattr(console, "console_session_id", lambda: "csn_test")
    ctl_ch = console.worker_client.console_ctl_channel("csn_test")
    out_ch = console.worker_client.console_out_channel("csn_test")
    in_ch = console.worker_client.console_in_channel("csn_test")

    out_payload = json.dumps({"data": base64.b64encode(b"hello\n").decode()})
    messages = [
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "ready"})},
        {"type": "message", "channel": out_ch, "data": out_payload},
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "closed", "reason": "pty_eof"})},
    ]
    pubsub = FakePubSub(messages)
    redis = FakeRedis(pubsub)
    monkeypatch.setattr(console.worker_client, "get_worker_redis", lambda: redis)
    monkeypatch.setattr(console.worker_client, "_prepare_redis_client", redis)

    # Клиент шлёт одну команду, потом disconnect.
    incoming = [
        {"type": "websocket.receive", "text": "ls -la\n"},
        {"type": "websocket.disconnect"},
    ]
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"}, incoming=incoming)

    await asyncio.wait_for(console.server_console_ws(ws, "srv_console1"), timeout=5.0)

    assert ws.accepted
    # start опубликован в control.
    start = [m for ch, m in redis.published if ch == ctl_ch and '"start"' in m]
    assert start, "start control message not published"
    start_obj = json.loads(start[0])
    assert start_obj["server_id"] == "srv_console1"
    assert start_obj["actor_id"] == "usr_1"
    assert start_obj["account_id"] == "acc_1"
    # Креды едут ссылкой на Redis-stash, не plaintext'ом.
    assert start_obj["creds_stash_key"].startswith("dbos:console_creds:")
    assert "management_user" not in start_obj
    assert "password" not in start_obj
    # ввод клиента опубликован в in-канал (base64 от "ls -la\n").
    in_msgs = [m for ch, m in redis.published if ch == in_ch]
    assert in_msgs, "client input not forwarded to console:in"
    decoded = base64.b64decode(json.loads(in_msgs[0])["data"])
    assert decoded == b"ls -la\n"
    # вывод PTY доставлен клиенту.
    assert b"hello\n" in b"".join(ws.sent_bytes)
    # stop опубликован на disconnect.
    assert any(ch == ctl_ch and '"stop"' in m for ch, m in redis.published)
    # session_open + session_close в аудите, с account_id/login в details.
    actions = [e["action"] for e in _patch_console]
    assert "ssh_console.session_open" in actions
    assert "ssh_console.session_close" in actions
    opened = [e for e in _patch_console if e["action"] == "ssh_console.session_open"][0]
    assert opened["details"]["account_id"] == "acc_1"
    assert opened["details"]["login"] == "svc"


@pytest.mark.asyncio
async def test_managed_false_server_works(monkeypatch, _patch_console):
    """Сервер не prepared (`is_managed=False`) — консоль всё равно поднимается."""
    async def allow(*a, **k):
        return None

    async def load(*a, **k):
        return _make_server(is_managed=False)

    monkeypatch.setattr(console.permissions, "require_action", allow)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(console, "console_session_id", lambda: "csn_unmanaged")

    ctl_ch = console.worker_client.console_ctl_channel("csn_unmanaged")
    messages = [
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "ready"})},
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "closed", "reason": "pty_eof"})},
    ]
    pubsub = FakePubSub(messages)
    redis = FakeRedis(pubsub)
    monkeypatch.setattr(console.worker_client, "get_worker_redis", lambda: redis)
    monkeypatch.setattr(console.worker_client, "_prepare_redis_client", redis)

    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await asyncio.wait_for(console.server_console_ws(ws, "srv_console1"), timeout=5.0)

    # accept'нут и start опубликован — никакого PREPARE_REQUIRED.
    assert ws.accepted
    assert ws.closed_reason != "PREPARE_REQUIRED"
    assert any(ch == ctl_ch and '"start"' in m for ch, m in redis.published)


@pytest.mark.asyncio
async def test_bridge_start_timeout_closes_with_error(monkeypatch, _patch_console):
    async def allow(*a, **k):
        return None

    async def load(*a, **k):
        return _make_server()

    monkeypatch.setattr(console.permissions, "require_action", allow)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(console, "console_session_id", lambda: "csn_to")
    monkeypatch.setattr(console, "_READY_TIMEOUT_SECONDS", 0.2)

    ctl_ch = console.worker_client.console_ctl_channel("csn_to")
    # Worker отвечает error вместо ready.
    messages = [
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "error", "error_code": "SSH_MANAGEMENT_KEY_MISSING"})},
    ]
    pubsub = FakePubSub(messages)
    redis = FakeRedis(pubsub)
    monkeypatch.setattr(console.worker_client, "get_worker_redis", lambda: redis)
    monkeypatch.setattr(console.worker_client, "_prepare_redis_client", redis)

    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await asyncio.wait_for(console.server_console_ws(ws, "srv_console1"), timeout=5.0)
    assert ws.accepted
    # error-кадр отправлен клиенту.
    assert any("error" in t for t in ws.sent_text)
    # session_close с reason start_failed.
    close = [e for e in _patch_console if e["action"] == "ssh_console.session_close"]
    assert close and close[0]["details"]["reason"].startswith("start_failed")
