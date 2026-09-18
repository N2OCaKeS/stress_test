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
  * занят другим пользователем → close 4409 SERVER_BUSY + denied-аудит;
  * занят самим caller'ом / свободен → бронь-гейт пропускает;
  * managed=False сервер → консоль работает (prepare не нужен);
  * happy-path bridge: креды аккаунта стэшатся в Redis, после `ready` ввод
    клиента публикуется в `console:in:<sid>`, вывод из `console:out:<sid>`
    уходит клиенту, на disconnect публикуется `stop`; start несёт
    `creds_stash_key`, аудит несёт `account_id`/`login`;
  * занят тестом (`busy_state=testing`) → консоль НЕ блокируется, но
    `account_id` из query игнорируется — креды берутся из
    `server_test_credentials` (`pft_svc.read_test_credentials`), аудит несёт
    `credentials_source=test`;
  * `testing` без серверного `(server, console)` → close 4403 (account-level
    обход тут не при чём, аккаунт-то не выбор пользователя);
  * `testing` без тестовых кред на сервере → close 4409
    TEST_CREDENTIALS_NOT_FOUND, без тихого фолбэка на `account_id`.

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
from src.core.constants import BusyState, ServerStatus
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


def _make_server(
    *,
    is_managed=True,
    status=ServerStatus.ONLINE,
    busy_state=BusyState.FREE,
    busy_user_id=None,
):
    return SimpleNamespace(
        id="srv_console1",
        hostname="host.example",
        ip_address="10.20.30.40",
        ssh_port=22,
        is_managed=is_managed,
        management_user="dbos",
        department_id="dep_a",
        status=status,
        busy_state=busy_state,
        busy_user_id=busy_user_id,
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

    # По умолчанию у caller'а есть серверный `(server, console)` — тесты под
    # отказ переопределяют. Серверный console больше не жёсткий require, а
    # has_resource_action-проверка (тип-wide ИЛИ инстанс-грант на сервер),
    # чей результат прокидывается в резолвер кред.
    async def fake_has_resource_action(*a, **k):
        return True

    monkeypatch.setattr(
        console.permissions, "has_resource_action", fake_has_resource_action
    )

    # Дефолтный happy-резолв кред + no-op Redis-stash (отдельные тесты
    # переопределяют под свои сценарии).
    async def fake_resolve(db, identity, account_id, server, **kwargs):
        return {"login": "svc", "password": "pw", "ssh_private_key": None}

    async def fake_store(stash_key, creds):
        return None

    async def fake_delete(stash_key):
        return None

    monkeypatch.setattr(
        console.account_svc, "resolve_console_credentials", fake_resolve,
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
    """Нет ни серверного `(server, console)`, ни права на учётку → 4403.

    Серверный console больше не жёсткий гейт: его отсутствие проверяет
    `has_action` (False), после чего авторизацию решает резолвер кред учётки —
    он и отбивает denied'ом, когда на учётке тоже нет console/view_password.
    """
    async def no_server_console(*a, **k):
        return False

    async def load(*a, **k):
        return _make_server()

    async def resolve_denied(*a, **k):
        raise AuthorizationError(error_code="PERMISSION_DENIED", message="no")

    monkeypatch.setattr(console.permissions, "has_resource_action", no_server_console)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(
        console.account_svc, "resolve_console_credentials", resolve_denied,
    )
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
    """Нет серверного console и нет права на креды учётки → 4403."""
    async def no_server_console(*a, **k):
        return False

    async def load(*a, **k):
        return _make_server()

    async def resolve_denied(*a, **k):
        raise AuthorizationError(error_code="PERMISSION_DENIED", message="no creds")

    monkeypatch.setattr(console.permissions, "has_resource_action", no_server_console)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(
        console.account_svc, "resolve_console_credentials", resolve_denied,
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
        console.account_svc, "resolve_console_credentials", resolve_notfound,
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
        console.account_svc, "resolve_console_credentials", resolve_no_pw,
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
async def test_busy_by_other_user_closes_conflict(monkeypatch, _patch_console):
    """Сервер занят другим пользователем → close 4409 SERVER_BUSY + denied."""
    async def load(*a, **k):
        return _make_server(busy_state=BusyState.BUSY, busy_user_id="usr_other")

    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_CONFLICT
    assert ws.closed_reason == "SERVER_BUSY"
    assert not ws.accepted
    assert any(
        e["action"] == "ssh_console.session_open"
        and e["status"] == "denied"
        and e.get("details", {}).get("reason") == "server_busy"
        and e.get("details", {}).get("busy_user_id") == "usr_other"
        for e in _patch_console
    )


@pytest.mark.asyncio
async def test_busy_by_self_passes_gate(monkeypatch, _patch_console):
    """Сервер забронирован самим caller'ом → бронь-гейт пропускает.

    Доходим до резолва кред: подменяем его отказом, чтобы не гонять мост, но
    закрытие идёт уже НЕ с SERVER_BUSY — гейт брони пройден.
    """
    async def load(*a, **k):
        return _make_server(busy_state=BusyState.BUSY, busy_user_id="usr_1")

    async def resolve_denied(*a, **k):
        raise AuthorizationError(error_code="PERMISSION_DENIED", message="no")

    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(
        console.account_svc, "resolve_console_credentials", resolve_denied,
    )
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_reason != "SERVER_BUSY"
    assert ws.closed_code == console._WS_CLOSE_FORBIDDEN


@pytest.mark.asyncio
async def test_testing_without_server_console_closes_4403(monkeypatch, _patch_console):
    """`testing` + нет серверного console → 4403, аккаунт-обход не выручает.

    В отличие от обычного гейта, при `testing` account-level `view_password`
    на аккаунте не спасает — учётка вызывающего вообще не используется.
    """
    async def no_server_console(*a, **k):
        return False

    async def load(*a, **k):
        return _make_server(busy_state=BusyState.TESTING)

    async def resolve_should_not_be_called(*a, **k):
        raise AssertionError("resolve_console_credentials must not be called for testing")

    monkeypatch.setattr(console.permissions, "has_resource_action", no_server_console)
    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(
        console.account_svc, "resolve_console_credentials", resolve_should_not_be_called,
    )
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
async def test_testing_missing_test_credentials_closes_4409(monkeypatch, _patch_console):
    """`testing` без строки в `server_test_credentials` → 4409, без фолбэка."""
    async def load(*a, **k):
        return _make_server(busy_state=BusyState.TESTING)

    async def no_test_creds(*a, **k):
        return None

    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(console.pft_svc, "read_test_credentials", no_test_creds)
    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await console.server_console_ws(ws, "srv_console1")
    assert ws.closed_code == console._WS_CLOSE_CONFLICT
    assert ws.closed_reason == "TEST_CREDENTIALS_NOT_FOUND"
    assert not ws.accepted


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
    monkeypatch.setattr(console.worker_client, "_creds_redis_client", redis)

    # Клиент шлёт одну команду, потом disconnect.
    incoming = [
        {"type": "websocket.receive", "text": "ls -la\n"},
        {"type": "websocket.disconnect"},
    ]
    ws = FakeWebSocket(
        headers={"Authorization": "Bearer tok"},
        subprotocols=["console.v1", "bearer.tok"],
        incoming=incoming,
    )

    await asyncio.wait_for(console.server_console_ws(ws, "srv_console1"), timeout=5.0)

    assert ws.accepted
    # Сервер обязан подтвердить один из предложенных клиентом subprotocol'ов,
    # иначе браузер рвёт handshake (close 1006).
    assert ws.accepted_subprotocol == "console.v1"
    # start опубликован в control.
    start = [m for ch, m in redis.published if ch == ctl_ch and '"start"' in m]
    assert start, "start control message not published"
    start_obj = json.loads(start[0])
    assert start_obj["server_id"] == "srv_console1"
    # Worker коннектится по IP, а не по короткому hostname.
    assert start_obj["host"] == "10.20.30.40"
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
    monkeypatch.setattr(console.worker_client, "_creds_redis_client", redis)

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
    monkeypatch.setattr(console.worker_client, "_creds_redis_client", redis)

    ws = FakeWebSocket(headers={"Authorization": "Bearer tok"})
    await asyncio.wait_for(console.server_console_ws(ws, "srv_console1"), timeout=5.0)
    assert ws.accepted
    # error-кадр отправлен клиенту.
    assert any("error" in t for t in ws.sent_text)
    # session_close с reason start_failed.
    close = [e for e in _patch_console if e["action"] == "ssh_console.session_close"]
    assert close and close[0]["details"]["reason"].startswith("start_failed")


@pytest.mark.asyncio
async def test_testing_uses_forced_test_credentials(monkeypatch, _patch_console):
    """`busy_state=testing` — доступ не блокируется, но подключение идёт под
    учёткой теста, а не под `account_id` из query.
    """
    async def load(*a, **k):
        return _make_server(busy_state=BusyState.TESTING)

    async def resolve_should_not_be_called(*a, **k):
        raise AssertionError("resolve_console_credentials must not be called for testing")

    async def fake_test_creds(db, server_id):
        assert server_id == "srv_console1"
        return {
            "username": "test_runner",
            "password": "test-pw",
            "ssh_public_key": "ssh-ed25519 AAAA...",
            "ssh_private_key": None,
            "rotated_at": None,
        }

    monkeypatch.setattr(console.server_svc, "load_visible_server", load)
    monkeypatch.setattr(
        console.account_svc, "resolve_console_credentials", resolve_should_not_be_called,
    )
    monkeypatch.setattr(console.pft_svc, "read_test_credentials", fake_test_creds)
    monkeypatch.setattr(console, "console_session_id", lambda: "csn_testing")

    ctl_ch = console.worker_client.console_ctl_channel("csn_testing")
    messages = [
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "ready"})},
        {"type": "message", "channel": ctl_ch, "data": json.dumps({"event": "closed", "reason": "pty_eof"})},
    ]
    pubsub = FakePubSub(messages)
    redis = FakeRedis(pubsub)
    monkeypatch.setattr(console.worker_client, "get_worker_redis", lambda: redis)
    monkeypatch.setattr(console.worker_client, "_creds_redis_client", redis)

    # `account_id` из query — заведомо чужой/произвольный, должен быть
    # полностью проигнорирован при резолве кред.
    ws = FakeWebSocket(
        headers={"Authorization": "Bearer tok"},
        query_params={"account_id": "acc_whatever"},
    )
    await asyncio.wait_for(console.server_console_ws(ws, "srv_console1"), timeout=5.0)

    assert ws.accepted
    start = [m for ch, m in redis.published if ch == ctl_ch and '"start"' in m]
    assert start, "start control message not published"

    opened = [e for e in _patch_console if e["action"] == "ssh_console.session_open"][0]
    assert opened["status"] == "success"
    assert opened["details"]["login"] == "test_runner"
    assert opened["details"]["credentials_source"] == "test"
    # account_id остаётся в аудите как «что запрашивал вызывающий», но креды
    # реально резолвились не через него.
    assert opened["details"]["account_id"] == "acc_whatever"

    closed = [e for e in _patch_console if e["action"] == "ssh_console.session_close"][0]
    assert closed["details"]["credentials_source"] == "test"
