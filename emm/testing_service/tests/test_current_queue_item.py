"""Тесты `GET /test-stands/{id}/current-queue-item` (§8.6 плана миграции).

Сигнал фронтенду («есть активный item — можно показать кнопку живого лога»).
Queue item заводится напрямую через репозиторий — полный prepare-цикл здесь
не при чём, важно только состояние/наличие строки.
"""

from __future__ import annotations

from src.core.constants import QueueItemState
from src.db.session import AsyncSessionLocal
from src.repositories import queue_item as queue_item_repo
from src.utils.ids import queue_item_id as new_queue_item_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    LAUNCH_CTX,
    _create_stand,
    _create_test_def,
    mock_server_service,
    recorded_calls,
)

STANDS_BASE = "/api/testing/v1/test-stands"


async def _make_stand_and_test(client, admin_token, mock_server_service):
    mock_server_service()
    stand_id, server_id = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    return stand_id, server_id, test_id


async def _create_queue_item(stand_id: str, test_id: str, *, state: str) -> str:
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


class TestCurrentQueueItem:
    async def test_no_active_item_returns_null(self, client, admin_token, mock_server_service):
        stand_id, _server_id, _test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        resp = await client.get(f"{STANDS_BASE}/{stand_id}/current-queue-item", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() is None

    async def test_active_item_returned(self, client, admin_token, mock_server_service):
        stand_id, _server_id, test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        item_id = await _create_queue_item(stand_id, test_id, state=QueueItemState.RUNNING)

        resp = await client.get(f"{STANDS_BASE}/{stand_id}/current-queue-item", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["queue_item_id"] == item_id
        assert body["state"] == "running"
        assert body["test_id"] == test_id

    async def test_terminal_item_not_returned(self, client, admin_token, mock_server_service):
        stand_id, _server_id, test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        await _create_queue_item(stand_id, test_id, state=QueueItemState.SUCCEEDED)

        resp = await client.get(f"{STANDS_BASE}/{stand_id}/current-queue-item", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() is None

    async def test_unknown_stand_404(self, client, admin_token):
        resp = await client.get(
            f"{STANDS_BASE}/stand_does_not_exist/current-queue-item", headers=_hdr(admin_token),
        )
        assert resp.status_code == 404, resp.text

    async def test_requires_auth(self, client):
        resp = await client.get(f"{STANDS_BASE}/stand_x/current-queue-item")
        assert resp.status_code == 401, resp.text


class TestListByServerId:
    async def test_filters_to_the_one_stand(self, client, admin_token, mock_server_service):
        stand_id, server_id, _test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        other_stand_id, _other_server_id, _other_test_id = await _make_stand_and_test(
            client, admin_token, mock_server_service,
        )

        resp = await client.get(
            STANDS_BASE, headers=_hdr(admin_token), params={"server_id": server_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert [i["id"] for i in body["items"]] == [stand_id]
        assert other_stand_id not in [i["id"] for i in body["items"]]

    async def test_unknown_server_id_returns_empty(self, client, admin_token):
        resp = await client.get(
            STANDS_BASE, headers=_hdr(admin_token), params={"server_id": "srv_does_not_exist"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []
