"""Тесты `GET /api/testing/v1/pool-overview` (§F плана 2026-09-11).

Покрывает:

* пустой пул/очередь — нули, не 500/null;
* очередь: `remaining` (queued+preparing+ready) отдельно от `running`;
* успешные/упавшие — по последней попытке логического теста, не по каждой
  попытке цепочки retry;
* три контекста (`all`/`run`/`standalone`) не путают числа друг друга;
* приоритет статусов стенда (восстановление → недоступен → тест идёт →
  готов), включая «нет данных» при отсутствии/устаревании ping;
* department-изоляция.

`server_client.get_servers_status_batch` мокается напрямую (не полный
MockTransport HTTP-стек — это чтение живых сигналов, не бизнес-транзакция).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.db.session import AsyncSessionLocal
from src.models import QueueItem, TestRun
from src.services import server_client
from tests.conftest import auth_hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    _create_stand,
    _create_test_def,
    mock_server_service,
    recorded_calls,
)

BASE = "/api/testing/v1/pool-overview"
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def server_mock(mock_server_service):
    mock_server_service()


@pytest.fixture(autouse=True)
def no_stand_status(monkeypatch):
    """Дефолт для тестов, которым статусы стендов не важны — пустой пул."""
    monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={}))


async def _add_item(
    *, stand_id: str, test_id: str, state: str,
    test_run_id: str | None = None, retry_of_id: str | None = None,
    debug_mode: bool = False, created_at: datetime | None = None,
) -> str:
    item_id = f"qi_pool_{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        db.add(QueueItem(
            id=item_id, stand_id=stand_id, test_id=test_id, launch_context={},
            state=state, test_run_id=test_run_id, retry_of_id=retry_of_id,
            debug_mode=debug_mode, created_by="usr_pool",
            created_at=created_at or NOW,
        ))
        await db.flush()
        await db.commit()
    return item_id


async def _add_test_run(*, department_id: str = "dep_a", status: str = "running") -> str:
    run_id = f"run_pool_{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        db.add(TestRun(
            id=run_id, os_version_id="osv_1.8.5", mode="orel", kernel="6.1.0",
            kernels=["6.1.0"], department_id=department_id, test_run_stands=[],
            status=status, created_by="usr_pool",
        ))
        await db.flush()
        await db.commit()
    return run_id


class TestEmptyPool:
    async def test_empty_pool_and_queue_are_zeros_not_error(self, client, admin_token):
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["context"] == "all"
        assert body["remaining"] == 0
        assert body["running"] == 0
        assert body["succeeded"] == 0
        assert body["failed"] == 0
        assert body["stands"] == []
        assert body["stand_status_counts"] == {
            "recovering": 0, "unreachable": 0, "testing": 0, "testing_done": 0, "ready": 0, "no_data": 0,
        }


class TestQueueCounts:
    async def test_remaining_and_running_are_separate(self, client, admin_token):
        stand, _ = await _create_stand(client, admin_token)
        test = await _create_test_def(client, admin_token, stand)
        for state in ("queued", "preparing", "ready", "running", "running"):
            await _add_item(stand_id=stand, test_id=test, state=state)
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        body = resp.json()
        assert body["remaining"] == 3
        assert body["running"] == 2

    async def test_success_and_failure_count_last_attempt_only(self, client, admin_token):
        stand, _ = await _create_stand(client, admin_token)
        test = await _create_test_def(client, admin_token, stand)
        first = await _add_item(stand_id=stand, test_id=test, state="failed")
        # Retry исправил тест — цепочка должна засчитаться как succeeded, а не
        # как failed+succeeded.
        await _add_item(stand_id=stand, test_id=test, state="succeeded", retry_of_id=first)
        # Независимая (без retry) вторая цепочка — падает окончательно.
        await _add_item(stand_id=stand, test_id=test, state="failed")

        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        body = resp.json()
        assert body["succeeded"] == 1
        assert body["failed"] == 1

    async def test_pagination_of_underlying_lists_does_not_skew_sums(self, client, admin_token):
        """Обзор — это агрегат, не постраничный список: много item'ов не искажает суммы."""
        stand, _ = await _create_stand(client, admin_token)
        test = await _create_test_def(client, admin_token, stand)
        for _ in range(37):
            await _add_item(stand_id=stand, test_id=test, state="succeeded")
        for _ in range(13):
            await _add_item(stand_id=stand, test_id=test, state="failed")
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        body = resp.json()
        assert body["succeeded"] == 37
        assert body["failed"] == 13


class TestContexts:
    async def test_run_context_scopes_to_one_campaign(self, client, admin_token):
        stand, _ = await _create_stand(client, admin_token)
        test = await _create_test_def(client, admin_token, stand)
        run_a = await _add_test_run()
        run_b = await _add_test_run()
        await _add_item(stand_id=stand, test_id=test, state="succeeded", test_run_id=run_a)
        await _add_item(stand_id=stand, test_id=test, state="failed", test_run_id=run_b)
        await _add_item(stand_id=stand, test_id=test, state="queued")  # standalone, не должен попасть

        resp = await client.get(BASE, headers=auth_hdr(admin_token), params={"context": "run", "test_run_id": run_a})
        body = resp.json()
        assert body["succeeded"] == 1
        assert body["failed"] == 0
        assert body["remaining"] == 0
        assert body["test_run"]["id"] == run_a

    async def test_standalone_context_excludes_campaigns(self, client, admin_token):
        stand, _ = await _create_stand(client, admin_token)
        test = await _create_test_def(client, admin_token, stand)
        run_a = await _add_test_run()
        await _add_item(stand_id=stand, test_id=test, state="succeeded", test_run_id=run_a)
        await _add_item(stand_id=stand, test_id=test, state="failed")

        resp = await client.get(BASE, headers=auth_hdr(admin_token), params={"context": "standalone"})
        body = resp.json()
        assert body["succeeded"] == 0
        assert body["failed"] == 1

    async def test_all_context_does_not_merge_success_and_failure_into_one_run(self, client, admin_token):
        stand, _ = await _create_stand(client, admin_token)
        test = await _create_test_def(client, admin_token, stand)
        run_a = await _add_test_run()
        await _add_item(stand_id=stand, test_id=test, state="succeeded", test_run_id=run_a)
        await _add_item(stand_id=stand, test_id=test, state="failed")
        resp = await client.get(BASE, headers=auth_hdr(admin_token), params={"context": "all"})
        body = resp.json()
        assert body["succeeded"] == 1
        assert body["failed"] == 1

    async def test_run_context_requires_test_run_id(self, client, admin_token):
        resp = await client.get(BASE, headers=auth_hdr(admin_token), params={"context": "run"})
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "TEST_RUN_ID_REQUIRED"

    async def test_test_run_id_rejected_outside_run_context(self, client, admin_token):
        run_a = await _add_test_run()
        resp = await client.get(BASE, headers=auth_hdr(admin_token), params={"context": "all", "test_run_id": run_a})
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "TEST_RUN_ID_NOT_ALLOWED"

    async def test_run_context_404_for_foreign_department(self, client, admin_token, make_token):
        run_a = await _add_test_run(department_id="dep_other")
        resp = await client.get(BASE, headers=auth_hdr(admin_token), params={"context": "run", "test_run_id": run_a})
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "TEST_RUN_NOT_FOUND"

    async def test_department_isolation(self, client, admin_token, make_token):
        stand, _ = await _create_stand(client, admin_token)
        test = await _create_test_def(client, admin_token, stand)
        await _add_item(stand_id=stand, test_id=test, state="failed")
        other = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})
        resp = await client.get(BASE, headers=auth_hdr(other))
        assert resp.json()["failed"] == 0

    async def test_invalid_time_range_rejected(self, client, admin_token):
        resp = await client.get(BASE, headers=auth_hdr(admin_token), params={
            "context": "standalone",
            "created_from": "2026-09-15T12:00:00+03:00",
            "created_until": "2026-09-15T10:00:00+03:00",
        })
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "INVALID_TIME_RANGE"

    async def test_requires_auth(self, client):
        assert (await client.get(BASE)).status_code == 401


def _fresh() -> str:
    """Момент "только что" — счёт свежести ping идёт от реального времени вызова, не от фиктивного `NOW`."""
    return datetime.now(timezone.utc).isoformat()


class TestStandStatuses:
    async def test_recovering_wins_over_negative_ping(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={
            server_id: {
                "server_id": server_id, "found": True, "busy_state": "acs",
                "ping_reachable": False, "ping_checked_at": _fresh(),
            },
        }))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        body = resp.json()
        assert body["stands"][0]["status"] == "recovering"
        assert body["stand_status_counts"]["recovering"] == 1

    async def test_unreachable_when_ping_negative_and_not_recovering(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={
            server_id: {
                "server_id": server_id, "found": True, "busy_state": "free",
                "ping_reachable": False, "ping_checked_at": _fresh(),
            },
        }))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.json()["stands"][0]["status"] == "unreachable"

    async def test_testing_when_busy_testing_and_reachable(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={
            server_id: {
                "server_id": server_id, "found": True, "busy_state": "testing",
                "ping_reachable": True, "ping_checked_at": _fresh(),
            },
        }))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.json()["stands"][0]["status"] == "testing"

    async def test_testing_done_when_busy_testing_done(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={
            server_id: {
                "server_id": server_id, "found": True, "busy_state": "testing_done",
                "ping_reachable": True, "ping_checked_at": _fresh(),
            },
        }))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        body = resp.json()
        assert body["stands"][0]["status"] == "testing_done"
        assert body["stand_status_counts"]["testing_done"] == 1

    async def test_ready_when_free_and_reachable(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={
            server_id: {
                "server_id": server_id, "found": True, "busy_state": "free",
                "ping_reachable": True, "ping_checked_at": _fresh(),
            },
        }))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.json()["stands"][0]["status"] == "ready"

    async def test_no_data_when_never_pinged(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={
            server_id: {
                "server_id": server_id, "found": True, "busy_state": "free",
                "ping_reachable": None, "ping_checked_at": None,
            },
        }))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.json()["stands"][0]["status"] == "no_data"

    async def test_no_data_when_ping_is_stale_not_fabricated_offline(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        stale_at = datetime.now(timezone.utc) - timedelta(hours=6)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={
            server_id: {
                "server_id": server_id, "found": True, "busy_state": "free",
                "ping_reachable": False, "ping_checked_at": stale_at.isoformat(),
            },
        }))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.json()["stands"][0]["status"] == "no_data"

    async def test_no_data_when_server_not_found_upstream(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={}))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.json()["stands"][0]["status"] == "no_data"

    async def test_inactive_stand_excluded(self, client, admin_token, monkeypatch):
        stand, server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(server_client, "get_servers_status_batch", AsyncMock(return_value={}))
        patch = await client.patch(
            f"/api/testing/v1/test-stands/{stand}", headers=auth_hdr(admin_token), json={"is_active": False},
        )
        assert patch.status_code == 200, patch.text
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.json()["stands"] == []
