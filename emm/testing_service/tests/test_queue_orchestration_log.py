"""Тесты диагностического лога оркестрации очереди стенда (P2-остаток, №4).

`queue_orchestration_events` объясняет «почему поллинг не привёл к запуску
теста» — до этой сущности такой ответ нигде не фиксировался. Кинды
`stand_busy_blocked`/`prepare_request_failed` заводятся через реальный поток
(`enqueue`/callback `prepare-for-test`, тем же MockTransport-приёмом, что и
`test_queue.py`); `item_stuck_in_head`/`claim_found_nothing_with_queue` —
через прямой вызов проверок сервисного слоя над item'ами с состаренным
`updated_at`, без гонки настоящих конкурентных `claim`-вызовов.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.core.constants import (
    CLAIM_DESYNC_GRACE_SECONDS,
    QUEUE_STUCK_THRESHOLD_SECONDS,
    QueueItemState,
)
from src.db.session import AsyncSessionLocal
from src.repositories import queue_item as queue_item_repo
from src.services import queue as queue_svc
from src.services import queue_orchestration_log as qol_svc
from src.utils.ids import queue_item_id as new_queue_item_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    CALLBACK_BASE,
    LAUNCH_CTX,
    SERVER_SECRET,
    _create_stand,
    _create_test_def,
    _identity,
    _server_hdr,
    configure_internal_keys,
    mock_server_service,
    recorded_calls,
)

STANDS_BASE = "/api/testing/v1/test-stands"


@pytest.fixture
def dept_b_admin(make_token) -> str:
    """`testing_service.admin`, но в чужом отделе — для проверки изоляции чтения лога."""
    return make_token(department_id="dep_b", service_roles={"testing_service": ["admin"]})


async def _make_stand_and_test(client, admin_token, mock_server_service):
    mock_server_service()
    stand_id, server_id = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    return stand_id, server_id, test_id


async def _create_queue_item(stand_id: str, test_id: str, *, state: str, updated_at: datetime | None = None) -> str:
    async with AsyncSessionLocal() as db:
        data = {
            "id": new_queue_item_id(),
            "stand_id": stand_id,
            "test_id": test_id,
            "launch_context": dict(LAUNCH_CTX),
            "state": state,
            "position": 0,
            "is_retry": False,
            "debug_mode": False,
            "created_by": "usr_test",
        }
        if updated_at is not None:
            data["updated_at"] = updated_at
        item = await queue_item_repo.create(db, data)
        await db.commit()
        return item.id


async def _log_kinds(client, admin_token, stand_id) -> list[dict]:
    resp = await client.get(f"{STANDS_BASE}/{stand_id}/orchestration-log", headers=_hdr(admin_token))
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestRecordAndReadEndpoint:
    async def test_record_then_read_via_endpoint(self, client, admin_token, mock_server_service):
        stand_id, _server_id, _test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        async with AsyncSessionLocal() as db:
            await qol_svc.record(db, stand_id, "prepare_request_failed", detail="boom")
            await db.commit()

        body = await _log_kinds(client, admin_token, stand_id)
        assert len(body) == 1
        assert body[0]["kind"] == "prepare_request_failed"
        assert body[0]["detail"] == "boom"
        assert body[0]["stand_id"] == stand_id
        assert body[0]["queue_item_id"] is None

    async def test_newest_first(self, client, admin_token, mock_server_service):
        stand_id, _server_id, _test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        for detail in ("first", "second"):
            async with AsyncSessionLocal() as db:
                await qol_svc.record(db, stand_id, "prepare_request_failed", detail=detail)
                await db.commit()
            await asyncio.sleep(0.02)  # разводит created_at, порядок не должен зависеть от совпавшей метки

        body = await _log_kinds(client, admin_token, stand_id)
        assert [e["detail"] for e in body] == ["second", "first"]

    async def test_unknown_stand_404(self, client, admin_token):
        resp = await client.get(
            f"{STANDS_BASE}/stand_does_not_exist/orchestration-log", headers=_hdr(admin_token),
        )
        assert resp.status_code == 404, resp.text

    async def test_requires_auth(self, client):
        resp = await client.get(f"{STANDS_BASE}/stand_x/orchestration-log")
        assert resp.status_code == 401, resp.text


class TestDepartmentIsolation:
    async def test_foreign_department_denied(self, client, admin_token, dept_b_admin, mock_server_service):
        stand_id, _server_id, _test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        resp = await client.get(
            f"{STANDS_BASE}/{stand_id}/orchestration-log", headers=_hdr(dept_b_admin),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"


class TestStandBusyBlocked:
    async def test_conflict_on_acquire_logs_stand_busy_blocked(self, client, admin_token, mock_server_service):
        mock_server_service(overrides={"acquire": 409})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert item.state == QueueItemState.FAILED

        body = await _log_kinds(client, admin_token, stand_id)
        busy_events = [e for e in body if e["kind"] == "stand_busy_blocked"]
        # Исходная попытка + попытка retry-item'а — обе разбились об один и
        # тот же 409, у обеих должен остаться свой queue_item_id.
        assert len(busy_events) == 2
        assert {e["queue_item_id"] for e in busy_events} != {None}
        assert all("SERVER_ALREADY_BUSY" in e["detail"] for e in busy_events)


class TestPrepareRequestFailed:
    async def test_non_conflict_failure_on_prepare_call_logged(self, client, admin_token, mock_server_service):
        mock_server_service(overrides={"prepare": 500})
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert item.state == QueueItemState.FAILED

        body = await _log_kinds(client, admin_token, stand_id)
        failed_events = [e for e in body if e["kind"] == "prepare_request_failed"]
        assert failed_events
        assert all("SERVER_SERVICE_ERROR" in e["detail"] for e in failed_events)
        # 409-конфликт здесь ни при чём — не должен попасть под этот kind.
        assert not any(e["kind"] == "stand_busy_blocked" for e in body)

    async def test_async_callback_failure_logged(
        self, client, admin_token, mock_server_service, configure_internal_keys,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert item.state == QueueItemState.PREPARING

        resp = await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": False, "failed_step": "restore", "error": "disk full"},
        )
        assert resp.status_code == 200, resp.text

        body = await _log_kinds(client, admin_token, stand_id)
        failed_events = [e for e in body if e["kind"] == "prepare_request_failed" and e["queue_item_id"] == item.id]
        assert len(failed_events) == 1
        assert "disk full" in failed_events[0]["detail"]


class TestItemStuckInHead:
    async def test_stuck_head_item_logged_once(self, client, admin_token, mock_server_service):
        stand_id, _server_id, test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        stale = datetime.now(timezone.utc) - timedelta(seconds=QUEUE_STUCK_THRESHOLD_SECONDS + 60)
        item_id = await _create_queue_item(stand_id, test_id, state=QueueItemState.PREPARING, updated_at=stale)

        async with AsyncSessionLocal() as db:
            await qol_svc.check_stuck_items(db)
            await db.commit()
        # Второй проход не должен задвоить событие по тому же item'у.
        async with AsyncSessionLocal() as db:
            await qol_svc.check_stuck_items(db)
            await db.commit()

        body = await _log_kinds(client, admin_token, stand_id)
        stuck = [e for e in body if e["kind"] == "item_stuck_in_head"]
        assert len(stuck) == 1
        assert stuck[0]["queue_item_id"] == item_id

    async def test_fresh_head_item_not_flagged(self, client, admin_token, mock_server_service):
        stand_id, _server_id, test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        await _create_queue_item(stand_id, test_id, state=QueueItemState.READY)

        async with AsyncSessionLocal() as db:
            await qol_svc.check_stuck_items(db)
            await db.commit()

        body = await _log_kinds(client, admin_token, stand_id)
        assert not any(e["kind"] == "item_stuck_in_head" for e in body)


class TestClaimFoundNothingWithQueue:
    async def test_stray_ready_item_logged_once(self, client, admin_token, mock_server_service):
        stand_id, _server_id, test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        stale = datetime.now(timezone.utc) - timedelta(seconds=CLAIM_DESYNC_GRACE_SECONDS + 5)
        item_id = await _create_queue_item(stand_id, test_id, state=QueueItemState.READY, updated_at=stale)

        async with AsyncSessionLocal() as db:
            await qol_svc.check_claim_desync(db)
            await db.commit()
        async with AsyncSessionLocal() as db:
            await qol_svc.check_claim_desync(db)
            await db.commit()

        body = await _log_kinds(client, admin_token, stand_id)
        stray = [e for e in body if e["kind"] == "claim_found_nothing_with_queue"]
        assert len(stray) == 1
        assert stray[0]["queue_item_id"] == item_id

    async def test_recent_ready_item_within_grace_not_flagged(self, client, admin_token, mock_server_service):
        stand_id, _server_id, test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        await _create_queue_item(stand_id, test_id, state=QueueItemState.READY)

        async with AsyncSessionLocal() as db:
            await qol_svc.check_claim_desync(db)
            await db.commit()

        body = await _log_kinds(client, admin_token, stand_id)
        assert not any(e["kind"] == "claim_found_nothing_with_queue" for e in body)


class TestRetention:
    async def test_record_trims_to_the_configured_cap(self, client, admin_token, mock_server_service, monkeypatch):
        stand_id, _server_id, _test_id = await _make_stand_and_test(client, admin_token, mock_server_service)
        monkeypatch.setattr(qol_svc, "_MAX_EVENTS_PER_STAND", 3)

        for i in range(5):
            async with AsyncSessionLocal() as db:
                await qol_svc.record(db, stand_id, "prepare_request_failed", detail=f"e{i}")
                await db.commit()
            await asyncio.sleep(0.02)

        body = await _log_kinds(client, admin_token, stand_id)
        assert len(body) == 3
        assert [e["detail"] for e in body] == ["e4", "e3", "e2"]
