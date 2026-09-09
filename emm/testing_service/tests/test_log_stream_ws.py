"""Тесты WS `GET /queue-items/{id}/log/stream` — живой лог теста (§8.6 плана миграции).

Механизм — поллинг `test_log_blobs.content`, не pub/sub (см. модуль docstring
`api/v1/endpoints/test_logs.py::stream_log`). Хендлер вызывается напрямую с
фейковым WebSocket'ом — тот же приём, что и у `server_service` для
`test_console_ws.py` (реальный handshake конфликтует с async-DB-фикстурами).
"""

from __future__ import annotations

import asyncio

from starlette.websockets import WebSocketState

from src.api.v1.endpoints import test_logs as endpoint
from src.core.constants import QueueItemState
from src.db.session import AsyncSessionLocal
from src.repositories import queue_item as queue_item_repo
from src.services import test_log as test_log_svc
from src.utils.ids import queue_item_id as new_queue_item_id
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    LAUNCH_CTX,
    _create_stand,
    _create_test_def,
    mock_server_service,
    recorded_calls,
)


class FakeLogWebSocket:
    """Минимальный async WS: `receive()` блокируется до explicit-disconnect/таймаута.

    В отличие от фейка в `server_service/tests/test_console_ws.py` (который
    отдаёт disconnect сразу, как только очередь входящих опустела — там это
    подходит, т.к. каждый вызов `receive()` там означает реальный кадр или
    конец сессии), здесь `receive()` используется только как способ прервать
    ожидание на disconnect — если ничего не произошло, он должен молчать,
    пока не истечёт `timeout` у `asyncio.wait_for` в `_wait_disconnect_or_timeout`.
    """

    def __init__(self, *, headers=None, subprotocols=None):
        self.headers = headers or {}
        self.scope = {"subprotocols": subprotocols or []}
        self.application_state = WebSocketState.CONNECTING
        self.accepted = False
        self.accepted_subprotocol = None
        self.closed_code = None
        self.closed_reason = None
        self.sent_text: list[str] = []
        self._disconnect_event = asyncio.Event()

    async def accept(self, subprotocol=None):
        self.accepted = True
        self.accepted_subprotocol = subprotocol
        self.application_state = WebSocketState.CONNECTED

    async def close(self, code=1000, reason=None):
        self.closed_code = code
        self.closed_reason = reason
        self.application_state = WebSocketState.DISCONNECTED

    async def send_text(self, text):
        self.sent_text.append(text)

    async def receive(self):
        await self._disconnect_event.wait()
        return {"type": "websocket.disconnect"}

    def trigger_disconnect(self) -> None:
        self._disconnect_event.set()


async def _make_queue_item(client, admin_token, mock_server_service, *, state=QueueItemState.RUNNING) -> str:
    mock_server_service()
    stand_id, _server_id = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    async with AsyncSessionLocal() as db:
        item = await queue_item_repo.create(db, {
            "id": new_queue_item_id(),
            "stand_id": stand_id,
            "test_id": test_id,
            "launch_context": dict(LAUNCH_CTX),
            "state": state,
            "position": 0,
            "is_retry": False,
            "debug_mode": False,
            "created_by": "usr_test",
        })
        await db.commit()
        return item.id


class TestLogStreamAuth:
    async def test_missing_token_closes_4401(self):
        ws = FakeLogWebSocket()
        await endpoint.stream_log(ws, "qi_whatever")
        assert ws.closed_code == endpoint._WS_CLOSE_UNAUTHENTICATED
        assert not ws.accepted

    async def test_invalid_token_closes_4401(self):
        ws = FakeLogWebSocket(headers={"Authorization": "Bearer garbage-not-a-real-token-000000"})
        await endpoint.stream_log(ws, "qi_whatever")
        assert ws.closed_code == endpoint._WS_CLOSE_UNAUTHENTICATED
        assert not ws.accepted

    async def test_token_via_subprotocol_accepted(self, client, admin_token, mock_server_service):
        item_id = await _make_queue_item(client, admin_token, mock_server_service, state=QueueItemState.SUCCEEDED)
        ws = FakeLogWebSocket(subprotocols=["testing-log.v1", f"bearer.{admin_token}"])
        await asyncio.wait_for(endpoint.stream_log(ws, item_id), timeout=2.0)
        assert ws.accepted
        assert ws.accepted_subprotocol == "testing-log.v1"


class TestLogStreamContent:
    async def test_sends_existing_content_then_delta_then_closes_on_terminal(
        self, client, admin_token, mock_server_service, monkeypatch,
    ):
        monkeypatch.setattr(endpoint, "_LOG_STREAM_POLL_INTERVAL_SECONDS", 0.02)
        item_id = await _make_queue_item(client, admin_token, mock_server_service)

        async with AsyncSessionLocal() as db:
            await test_log_svc.append_chunk(db, item_id, "hello ")

        ws = FakeLogWebSocket(headers={"Authorization": f"Bearer {admin_token}"})
        task = asyncio.create_task(endpoint.stream_log(ws, item_id))
        try:
            await asyncio.sleep(0.1)
            assert ws.accepted
            assert "".join(ws.sent_text) == "hello "

            async with AsyncSessionLocal() as db:
                await test_log_svc.append_chunk(db, item_id, "world")
            await asyncio.sleep(0.1)
            assert "".join(ws.sent_text) == "hello world"

            async with AsyncSessionLocal() as db:
                obj = await queue_item_repo.get_by_id(db, item_id)
                await queue_item_repo.update(db, obj, {"state": QueueItemState.SUCCEEDED})
                await db.commit()

            await asyncio.wait_for(task, timeout=2.0)
        finally:
            if not task.done():
                task.cancel()
        assert ws.closed_code == endpoint._WS_CLOSE_NORMAL
        assert ws.closed_reason == "TEST_FINISHED"

    async def test_no_log_yet_waits_without_sending(self, client, admin_token, mock_server_service, monkeypatch):
        monkeypatch.setattr(endpoint, "_LOG_STREAM_POLL_INTERVAL_SECONDS", 0.02)
        item_id = await _make_queue_item(client, admin_token, mock_server_service)

        ws = FakeLogWebSocket(headers={"Authorization": f"Bearer {admin_token}"})
        task = asyncio.create_task(endpoint.stream_log(ws, item_id))
        try:
            await asyncio.sleep(0.1)
            assert ws.accepted
            assert ws.sent_text == []
            assert ws.closed_code is None
        finally:
            ws.trigger_disconnect()
            await asyncio.wait_for(task, timeout=2.0)

    async def test_unknown_queue_item_closes_4404(self, admin_token):
        ws = FakeLogWebSocket(headers={"Authorization": f"Bearer {admin_token}"})
        await asyncio.wait_for(endpoint.stream_log(ws, "qi_does_not_exist"), timeout=2.0)
        assert ws.closed_code == endpoint._WS_CLOSE_NOT_FOUND

    async def test_client_disconnect_stops_loop_cleanly(
        self, client, admin_token, mock_server_service, monkeypatch,
    ):
        monkeypatch.setattr(endpoint, "_LOG_STREAM_POLL_INTERVAL_SECONDS", 0.02)
        item_id = await _make_queue_item(client, admin_token, mock_server_service)
        ws = FakeLogWebSocket(headers={"Authorization": f"Bearer {admin_token}"})
        task = asyncio.create_task(endpoint.stream_log(ws, item_id))
        await asyncio.sleep(0.05)
        ws.trigger_disconnect()
        await asyncio.wait_for(task, timeout=2.0)
        assert task.exception() is None
        # Read-only disconnect клиента — не терминальное состояние теста,
        # хендлер просто прекращает поллинг без закрытия сокета своей стороной.
        assert ws.closed_code is None

    async def test_already_terminal_on_connect_flushes_and_closes_immediately(
        self, client, admin_token, mock_server_service,
    ):
        item_id = await _make_queue_item(client, admin_token, mock_server_service, state=QueueItemState.RUNNING)
        async with AsyncSessionLocal() as db:
            await test_log_svc.append_chunk(db, item_id, "already done")
            obj = await queue_item_repo.get_by_id(db, item_id)
            await queue_item_repo.update(db, obj, {"state": QueueItemState.FAILED})
            await db.commit()

        ws = FakeLogWebSocket(headers={"Authorization": f"Bearer {admin_token}"})
        await asyncio.wait_for(endpoint.stream_log(ws, item_id), timeout=2.0)
        assert "".join(ws.sent_text) == "already done"
        assert ws.closed_code == endpoint._WS_CLOSE_NORMAL
        assert ws.closed_reason == "TEST_FINISHED"
