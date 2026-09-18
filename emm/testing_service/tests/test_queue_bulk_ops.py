"""Bulk-операции над очередью стенда (`clear-queue`, `retry-failed`) и `delete`
одиночного элемента.

`clear-queue`/`retry-failed` живут на стенде (`/test-stands/{id}/...`),
`delete` — на самом элементе (`/queue-items/{id}`), тем же RESTful стилем,
что и `resume-queue`/`skip`/`pause`/`retry`. Все три используют тот же
допуск, что постановка в очередь: `authorize()`/`_authorize_item()` в
`services/public_queue.py`.
"""

from __future__ import annotations

import pytest

from src.core.constants import QueueItemState
from src.db.session import AsyncSessionLocal
from tests.conftest import auth_hdr
from tests.test_queue import (
    CALLBACK_BASE,
    QUEUE_BASE,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _get_item,
    _server_hdr,
    configure_internal_keys as configure_internal_keys,
    mock_git_token as mock_git_token,
    mock_server_service as mock_server_service,
    recorded_calls as recorded_calls,
)
from tests.test_queue_interrupt import _enqueue, _make_ready
from tests.test_test_runs import _drive_to_success, _drive_to_terminal_failure

STANDS = "/api/testing/v1/test-stands"
BASE = "/api/testing/v1/queue-items"


async def _disable_retry(department_id: str = "dep_a") -> None:
    """Отключить авто-retry отдела, чтобы провал item'а остался терминальным."""
    from src.repositories import department_test_settings as dts_repo
    from src.utils.ids import department_test_settings_id

    async with AsyncSessionLocal() as db:
        existing = await dts_repo.get_by_department(db, department_id)
        if existing is None:
            await dts_repo.create(db, {
                "id": department_test_settings_id(),
                "department_id": department_id,
                "retry_enabled": False,
            })
        else:
            await dts_repo.update(db, existing, {"retry_enabled": False})
        await db.commit()


class TestClearQueue:
    async def test_removes_only_not_yet_started_items(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        head = await _enqueue(test_id)
        second = await _enqueue(test_id)
        third = await _enqueue(test_id)
        assert head.state == QueueItemState.PREPARING
        assert second.state == QueueItemState.QUEUED
        assert third.state == QueueItemState.QUEUED
        recorded_calls.clear()

        resp = await client.post(f"{STANDS}/{stand_id}/clear-queue", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["cleared_count"] == 2

        assert await _get_item(second.id) is None
        assert await _get_item(third.id) is None
        # Головной элемент как готовился, так и готовится — очистка не про него.
        assert (await _get_item(head.id)).state == QueueItemState.PREPARING
        assert recorded_calls == []

    async def test_empty_queue_is_a_noop(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)

        resp = await client.post(f"{STANDS}/{stand_id}/clear-queue", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["cleared_count"] == 0

    async def test_does_not_touch_paused_item(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        """Пауза держит бронь стенда — `clear-queue` не должен её задевать."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await client.post(f"{BASE}/{item.id}/pause", headers=auth_hdr(admin_token))
        assert (await _get_item(item.id)).state == QueueItemState.PAUSED
        recorded_calls.clear()

        resp = await client.post(f"{STANDS}/{stand_id}/clear-queue", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["cleared_count"] == 0
        assert (await _get_item(item.id)).state == QueueItemState.PAUSED
        assert recorded_calls == []

    async def test_requires_rights_on_the_stand(
        self, client, admin_token, make_token, no_role_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        other = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})

        for token in (other, no_role_token):
            resp = await client.post(f"{STANDS}/{stand_id}/clear-queue", headers=auth_hdr(token))
            assert resp.status_code == 403, resp.text
        assert (await client.post(f"{STANDS}/{stand_id}/clear-queue")).status_code == 401

    async def test_unknown_stand_is_404(self, client, admin_token):
        resp = await client.post(f"{STANDS}/tst_does_not_exist/clear-queue", headers=auth_hdr(admin_token))
        assert resp.status_code == 404, resp.text


class TestRetryFailed:
    async def test_retries_every_failed_item_of_the_stand(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        await _disable_retry()
        stand_id, _ = await _create_stand(client, admin_token)
        test_a = await _create_test_def(client, admin_token, stand_id)
        test_b = await _create_test_def(client, admin_token, stand_id)

        item_a = await _enqueue(test_a, debug_mode=True, stand_id=stand_id)
        await _drive_to_terminal_failure(client, item_a)
        assert (await _get_item(item_a.id)).state == QueueItemState.FAILED

        item_b = await _enqueue(test_b, debug_mode=True, stand_id=stand_id)
        await _drive_to_terminal_failure(client, item_b)
        assert (await _get_item(item_b.id)).state == QueueItemState.FAILED

        resp = await client.post(f"{STANDS}/{stand_id}/retry-failed", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["retried_count"] == 2
        assert payload["skipped_count"] == 0
        retry_of_ids = {item["retry_of_id"] for item in payload["items"]}
        assert retry_of_ids == {item_a.id, item_b.id}

    async def test_retry_keeps_prepare_only(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        # Массовый retry не должен превращать провалившийся testenv-прогон
        # в обычный запуск теста — новый item обязан унаследовать prepare_only.
        mock_server_service()
        await mock_git_token()
        await _disable_retry()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        item = await _enqueue(test_id, debug_mode=True, stand_id=stand_id, prepare_only=True)
        await _drive_to_terminal_failure(client, item)
        assert (await _get_item(item.id)).state == QueueItemState.FAILED

        resp = await client.post(f"{STANDS}/{stand_id}/retry-failed", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["retried_count"] == 1
        assert resp.json()["items"][0]["prepare_only"] is True

    async def test_no_failed_items_is_a_noop(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)

        resp = await client.post(f"{STANDS}/{stand_id}/retry-failed", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"retried_count": 0, "skipped_count": 0, "items": []}

    async def test_skips_a_failed_item_already_retried(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        import uuid

        mock_server_service()
        await mock_git_token()
        await _disable_retry()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id, debug_mode=True, stand_id=stand_id)
        await _drive_to_terminal_failure(client, item)
        assert (await _get_item(item.id)).state == QueueItemState.FAILED

        manual_retry = await client.post(
            f"{BASE}/{item.id}/retry",
            headers=auth_hdr(admin_token),
            json={"request_id": uuid.uuid4().hex},
        )
        assert manual_retry.status_code == 201, manual_retry.text

        resp = await client.post(f"{STANDS}/{stand_id}/retry-failed", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["retried_count"] == 0
        assert payload["skipped_count"] == 1
        assert payload["items"] == []

    async def test_requires_rights_on_the_stand(
        self, client, admin_token, make_token, no_role_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        other = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})

        for token in (other, no_role_token):
            resp = await client.post(f"{STANDS}/{stand_id}/retry-failed", headers=auth_hdr(token))
            assert resp.status_code == 403, resp.text
        assert (await client.post(f"{STANDS}/{stand_id}/retry-failed")).status_code == 401


class TestDeleteQueueItem:
    async def test_removes_queued_item_without_disturbing_the_head(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        head = await _enqueue(test_id)
        second = await _enqueue(test_id)
        recorded_calls.clear()

        resp = await client.delete(f"{BASE}/{second.id}", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text

        assert await _get_item(second.id) is None
        assert (await _get_item(head.id)).state == QueueItemState.PREPARING
        assert recorded_calls == []

    async def test_removes_a_terminal_item(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await _drive_to_success(client, item)
        assert (await _get_item(item.id)).state == QueueItemState.SUCCEEDED

        resp = await client.delete(f"{BASE}/{item.id}", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert await _get_item(item.id) is None

    async def test_releases_the_stand_when_the_paused_item_is_removed(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await client.post(f"{BASE}/{item.id}/pause", headers=auth_hdr(admin_token))
        assert (await _get_item(item.id)).state == QueueItemState.PAUSED
        recorded_calls.clear()

        resp = await client.delete(f"{BASE}/{item.id}", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert await _get_item(item.id) is None
        assert any(p.endswith("/release-for-service") for _, p in recorded_calls)

    @pytest.mark.parametrize("state", ["preparing", "ready", "running"])
    async def test_rejects_an_item_that_is_still_in_progress(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, state,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        assert item.state == QueueItemState.PREPARING

        if state in ("ready", "running"):
            await _make_ready(client, item)
        if state == "running":
            resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
            assert resp.status_code == 200, resp.text

        resp = await client.delete(f"{BASE}/{item.id}", headers=auth_hdr(admin_token))
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "QUEUE_ITEM_IN_PROGRESS"
        assert (await _get_item(item.id)).state != QueueItemState.SUCCEEDED

    async def test_requires_rights_on_the_stand(
        self, client, admin_token, make_token, no_role_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        other = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})

        for token in (other, no_role_token):
            resp = await client.delete(f"{BASE}/{item.id}", headers=auth_hdr(token))
            assert resp.status_code == 403, resp.text
        assert (await client.delete(f"{BASE}/{item.id}")).status_code == 401
        assert (await _get_item(item.id)).state == QueueItemState.PREPARING

    async def test_unknown_item_is_404(self, client, admin_token):
        resp = await client.delete(f"{BASE}/qi_does_not_exist", headers=auth_hdr(admin_token))
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "QUEUE_ITEM_NOT_FOUND"

    async def test_removing_a_campaign_item_does_not_break_status_recompute(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        from tests.test_campaign_attempts import create_run, detail

        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)
        run_id = await create_run(client, admin_token, [stand_id])
        data = await detail(client, admin_token, run_id)
        item_id = data["queue_items"][0]["queue_item_id"]
        item = await _get_item(item_id)
        await _drive_to_success(client, item)

        resp = await client.delete(f"{BASE}/{item_id}", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert await _get_item(item_id) is None
