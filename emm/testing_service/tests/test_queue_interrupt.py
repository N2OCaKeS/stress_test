"""Управление очередью стенда: «Пропустить», «Остановить», «Продолжить».

Три стартовых состояния элемента ведут себя по-разному. `queued`/`preparing`/
`ready` — SSH-сессии на стенде ещё нет, прерывать физически нечего, переход
происходит сразу. `running` — сессия идёт, и оборвать её может только
`testing_worker`: публичный эндпоинт оставляет заявку `interrupt_action`, а
терминальный переход приезжает обратно через `completed(interrupted=...)`.
"""

from __future__ import annotations

import pytest

from src.core.constants import QueueItemState
from src.core.constants import TestRunStatus as RunStatus
from src.db.session import AsyncSessionLocal
from src.services import queue as queue_svc
from src.services import test_run_status
from tests.conftest import auth_hdr
from tests.test_queue import (
    CALLBACK_BASE,
    LAUNCH_CTX,
    QUEUE_BASE,
    SERVER_SECRET,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _get_item,
    _identity,
    _server_hdr,
    configure_internal_keys as configure_internal_keys,
    mock_git_token as mock_git_token,
    mock_server_service as mock_server_service,
    recorded_calls as recorded_calls,
)

BASE = "/api/testing/v1/queue-items"
STANDS = "/api/testing/v1/test-stands"


async def _enqueue(test_id: str, **kwargs):
    async with AsyncSessionLocal() as db:
        return await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX, **kwargs)


async def _make_ready(client, item):
    """Провести элемент через callback prepare-for-test до `ready`."""
    resp = await client.post(
        f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
        headers=_server_hdr("server_service", SERVER_SECRET),
        json={
            "correlation_id": item.id, "succeeded": True,
            "test_username": "u", "test_password": "s3cr3t",
            "test_ssh_private_key": "-----KEY-----",
        },
    )
    assert resp.status_code == 200, resp.text


async def _make_running(client, item):
    """`ready` → `running`: воркер забрал задание."""
    await _make_ready(client, item)
    resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["queue_item_id"] == item.id


class TestImmediateInterrupt:
    """Элемент ещё не исполняется — `skip`/`pause` срабатывают без воркера."""

    @pytest.mark.parametrize("action,expected", [
        ("skip", QueueItemState.SKIPPED),
        ("pause", QueueItemState.PAUSED),
    ])
    async def test_preparing_item(
        self, client, admin_token, mock_server_service, recorded_calls, action, expected,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        assert item.state == QueueItemState.PREPARING
        recorded_calls.clear()

        resp = await client.post(f"{BASE}/{item.id}/{action}", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == expected
        assert resp.json()["interrupt_action"] is None

        assert (await _get_item(item.id)).state == expected
        # Пропуск освобождает стенд (очередь пуста — бронь снимается), пауза
        # оставляет стенд за собой и ждёт resume-queue.
        released = any(p.endswith("/release-for-service") for _, p in recorded_calls)
        assert released is (action == "skip")

    @pytest.mark.parametrize("action,expected", [
        ("skip", QueueItemState.SKIPPED),
        ("pause", QueueItemState.PAUSED),
    ])
    async def test_ready_item(
        self, client, admin_token, mock_server_service, configure_internal_keys, action, expected,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await _make_ready(client, item)
        assert (await _get_item(item.id)).state == QueueItemState.READY

        resp = await client.post(f"{BASE}/{item.id}/{action}", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == expected

        updated = await _get_item(item.id)
        assert updated.state == expected
        # Креды снятого с исполнения элемента больше не нужны — стэш отпущен,
        # иначе воркер мог бы забрать его следующим claim'ом.
        assert updated.creds_stash_key is None

        claim = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert claim.json()["item"] is None

    @pytest.mark.parametrize("action,expected", [
        ("skip", QueueItemState.SKIPPED),
        ("pause", QueueItemState.PAUSED),
    ])
    async def test_queued_item_does_not_disturb_the_head_of_the_queue(
        self, client, admin_token, mock_server_service, recorded_calls, action, expected,
    ):
        """Второй элемент очереди снимается, пока первый готовится — цикл стенда не трогаем."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        head = await _enqueue(test_id)
        second = await _enqueue(test_id)
        assert head.state == QueueItemState.PREPARING
        assert second.state == QueueItemState.QUEUED
        recorded_calls.clear()

        resp = await client.post(f"{BASE}/{second.id}/{action}", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert (await _get_item(second.id)).state == expected

        # Головной элемент как готовился, так и готовится; ни второго
        # prepare-for-test, ни снятия брони из-под него не случилось.
        assert (await _get_item(head.id)).state == QueueItemState.PREPARING
        assert recorded_calls == []

    async def test_terminal_item_cannot_be_interrupted(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await _make_running(client, item)
        await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert (await _get_item(item.id)).state == QueueItemState.SUCCEEDED

        for action in ("skip", "pause"):
            resp = await client.post(f"{BASE}/{item.id}/{action}", headers=auth_hdr(admin_token))
            assert resp.status_code == 409, resp.text
            assert resp.json()["error_code"] == "QUEUE_ITEM_NOT_ACTIVE"

    async def test_requires_rights_on_the_stand(
        self, client, admin_token, make_token, no_role_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        other = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})

        for token in (other, no_role_token):
            resp = await client.post(f"{BASE}/{item.id}/skip", headers=auth_hdr(token))
            assert resp.status_code == 403, resp.text
        assert (await client.post(f"{BASE}/{item.id}/skip")).status_code == 401
        assert (await _get_item(item.id)).state == QueueItemState.PREPARING

    async def test_unknown_item_is_404(self, client, admin_token):
        resp = await client.post(f"{BASE}/qi_does_not_exist/skip", headers=auth_hdr(admin_token))
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "QUEUE_ITEM_NOT_FOUND"


class TestRunningInterrupt:
    """Элемент исполняется — заявка воркеру, терминальный переход через `completed`."""

    @pytest.mark.parametrize("action", ["skip", "pause"])
    async def test_request_leaves_item_running_with_interrupt_action(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, action,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await _make_running(client, item)

        resp = await client.post(f"{BASE}/{item.id}/{action}", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["state"] == QueueItemState.RUNNING
        assert payload["interrupt_action"] == action

        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.RUNNING
        assert updated.interrupt_action == action

        # Воркер видит ту же заявку через internal-канал.
        check = await client.get(
            f"{QUEUE_BASE}/{item.id}/interrupt-check",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
        )
        assert check.status_code == 200, check.text
        assert check.json() == {"action": action}

    async def test_skip_completes_terminally_and_advances_the_queue(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
        recorded_calls,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        follower = await _enqueue(test_id)
        await _make_running(client, item)
        await client.post(f"{BASE}/{item.id}/skip", headers=auth_hdr(admin_token))
        recorded_calls.clear()

        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": False, "exit_code": None, "error": None, "interrupted": "skip"},
        )
        assert resp.status_code == 200, resp.text

        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.SKIPPED
        assert updated.finished_at is not None
        # Пропуск — не провал: ни текста ошибки, ни retry-элемента.
        assert updated.error is None
        assert updated.interrupt_action is None

        # Стенд поехал дальше со следующим элементом очереди.
        assert (await _get_item(follower.id)).state == QueueItemState.PREPARING
        assert any(p.endswith("/prepare-for-test") for _, p in recorded_calls)

    async def test_natural_finish_clears_a_pending_interrupt_request(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        """Тест успел закончиться сам, пока заявка ехала до воркера — исход настоящий."""
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await _make_running(client, item)
        await client.post(f"{BASE}/{item.id}/skip", headers=auth_hdr(admin_token))

        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert resp.status_code == 200, resp.text

        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.SUCCEEDED
        assert updated.interrupt_action is None

    async def test_skip_does_not_create_a_retry(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        from sqlalchemy import select
        from src.models import QueueItem

        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await _make_running(client, item)
        await client.post(f"{BASE}/{item.id}/skip", headers=auth_hdr(admin_token))
        await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": False, "exit_code": 1, "error": "killed", "interrupted": "skip"},
        )

        async with AsyncSessionLocal() as db:
            successor = (await db.execute(
                select(QueueItem).where(QueueItem.retry_of_id == item.id)
            )).scalar_one_or_none()
        assert successor is None

    async def test_pause_stops_the_stand_without_advancing(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
        recorded_calls,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        follower = await _enqueue(test_id)
        await _make_running(client, item)
        await client.post(f"{BASE}/{item.id}/pause", headers=auth_hdr(admin_token))
        recorded_calls.clear()

        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": False, "exit_code": None, "error": None, "interrupted": "pause"},
        )
        assert resp.status_code == 200, resp.text

        updated = await _get_item(item.id)
        assert updated.state == QueueItemState.PAUSED
        assert updated.interrupt_action is None
        assert updated.finished_at is None

        # Стенд стоит: ни следующий элемент не подхвачен, ни бронь не снята.
        assert (await _get_item(follower.id)).state == QueueItemState.QUEUED
        assert recorded_calls == []

    @pytest.mark.parametrize("action", ["skip", "pause"])
    async def test_duplicate_completed_is_a_no_op(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, action,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await _make_running(client, item)
        await client.post(f"{BASE}/{item.id}/{action}", headers=auth_hdr(admin_token))

        body = {"succeeded": False, "exit_code": None, "error": None, "interrupted": action}
        for _ in range(2):
            resp = await client.post(
                f"{QUEUE_BASE}/{item.id}/completed",
                headers=_server_hdr("testing_worker", WORKER_SECRET), json=body,
            )
            assert resp.status_code == 200, resp.text

        expected = QueueItemState.SKIPPED if action == "skip" else QueueItemState.PAUSED
        assert (await _get_item(item.id)).state == expected

        # Обычный (не прерванный) дубль поверх уже обработанного прерывания
        # тоже ничего не меняет — элемент давно не `running`.
        resp = await client.post(
            f"{QUEUE_BASE}/{item.id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": True, "exit_code": 0},
        )
        assert resp.status_code == 200, resp.text
        assert (await _get_item(item.id)).state == expected


class TestInterruptCheck:
    async def test_wrong_identity_rejected(self, client, configure_internal_keys):
        resp = await client.get(
            f"{QUEUE_BASE}/qi_whatever/interrupt-check",
            headers=_server_hdr("server_service", SERVER_SECRET),
        )
        assert resp.status_code == 401, resp.text

    async def test_unknown_item_reads_as_no_interrupt(self, client, configure_internal_keys):
        resp = await client.get(
            f"{QUEUE_BASE}/qi_does_not_exist/interrupt-check",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"action": None}

    async def test_running_item_without_a_request_reads_as_null(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await _make_running(client, item)

        resp = await client.get(
            f"{QUEUE_BASE}/{item.id}/interrupt-check",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
        )
        assert resp.json() == {"action": None}


class TestResumeQueue:
    async def test_paused_item_goes_to_the_end_and_the_queue_continues(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        first = await _enqueue(test_id)
        second = await _enqueue(test_id)
        await client.post(f"{BASE}/{first.id}/pause", headers=auth_hdr(admin_token))
        assert (await _get_item(first.id)).state == QueueItemState.PAUSED
        recorded_calls.clear()

        resp = await client.post(f"{STANDS}/{stand_id}/resume-queue", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == first.id

        resumed = await _get_item(first.id)
        follower = await _get_item(second.id)
        # Возобновлённый встал в конец очереди — работать стенд начинает с того,
        # кто теперь идёт первым.
        assert resumed.position > follower.position
        assert resumed.state == QueueItemState.QUEUED
        assert follower.state == QueueItemState.PREPARING

    async def test_resume_of_the_only_item_restarts_its_own_cycle(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await client.post(f"{BASE}/{item.id}/pause", headers=auth_hdr(admin_token))

        resp = await client.post(f"{STANDS}/{stand_id}/resume-queue", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == QueueItemState.PREPARING
        assert (await _get_item(item.id)).state == QueueItemState.PREPARING

    async def test_stand_without_a_paused_item_is_409(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)

        resp = await client.post(f"{STANDS}/{stand_id}/resume-queue", headers=auth_hdr(admin_token))
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "STAND_NOT_PAUSED"

    async def test_unknown_stand_is_404(self, client, admin_token):
        resp = await client.post(
            f"{STANDS}/stand_does_not_exist/resume-queue", headers=auth_hdr(admin_token),
        )
        assert resp.status_code == 404, resp.text

    async def test_requires_rights_on_the_stand(
        self, client, admin_token, make_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        other = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})

        resp = await client.post(f"{STANDS}/{stand_id}/resume-queue", headers=auth_hdr(other))
        assert resp.status_code == 403, resp.text


class TestPausedOccupiesTheStand:
    async def test_new_item_queues_behind_a_paused_one(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        """`paused` держит стенд занятым — новая постановка не запускает второй цикл."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await client.post(f"{BASE}/{item.id}/pause", headers=auth_hdr(admin_token))
        recorded_calls.clear()

        fresh = await _enqueue(test_id)

        assert fresh.state == QueueItemState.QUEUED
        assert recorded_calls == []

    async def test_skipping_a_paused_item_frees_the_stand(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await client.post(f"{BASE}/{item.id}/pause", headers=auth_hdr(admin_token))
        recorded_calls.clear()

        resp = await client.post(f"{BASE}/{item.id}/skip", headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert (await _get_item(item.id)).state == QueueItemState.SKIPPED
        assert any(p.endswith("/release-for-service") for _, p in recorded_calls)


class TestCampaignAggregation:
    """`compute_status` — чистая агрегация, отдельный статус под пропуск не заводим."""

    def test_paused_item_keeps_the_campaign_running(self):
        assert test_run_status.compute_status([
            QueueItemState.PAUSED, QueueItemState.SUCCEEDED,
        ]) == RunStatus.RUNNING
        assert test_run_status.compute_status([QueueItemState.PAUSED]) == RunStatus.RUNNING

    def test_skipped_is_terminal_but_not_an_outcome(self):
        # Все элементы терминальны, исходы разные → partially_failed; отдельный
        # агрегатный статус «частично пропущено» осознанно не заводится.
        assert test_run_status.compute_status([
            QueueItemState.SKIPPED, QueueItemState.SUCCEEDED,
        ]) == RunStatus.PARTIALLY_FAILED
        assert test_run_status.compute_status([
            QueueItemState.SKIPPED, QueueItemState.FAILED,
        ]) == RunStatus.PARTIALLY_FAILED
        assert test_run_status.compute_status([
            QueueItemState.SKIPPED,
        ]) == RunStatus.PARTIALLY_FAILED

    def test_mixed_skipped_and_paused_is_still_running(self):
        assert test_run_status.compute_status([
            QueueItemState.SKIPPED, QueueItemState.PAUSED, QueueItemState.FAILED,
        ]) == RunStatus.RUNNING


class TestListFilters:
    async def test_new_states_are_accepted_by_the_list_filter(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        item = await _enqueue(test_id)
        await client.post(f"{BASE}/{item.id}/pause", headers=auth_hdr(admin_token))

        resp = await client.get(
            BASE, headers=auth_hdr(admin_token), params={"states": ["paused"], "kind": "all"},
        )
        assert resp.status_code == 200, resp.text
        ids = [row["id"] for row in resp.json()["items"]]
        assert item.id in ids
        assert resp.json()["items"][ids.index(item.id)]["interrupt_action"] is None

        resp = await client.get(
            BASE, headers=auth_hdr(admin_token), params={"states": ["skipped"], "kind": "all"},
        )
        assert resp.status_code == 200, resp.text
        assert all(row["id"] != item.id for row in resp.json()["items"])


async def test_skip_of_a_campaign_item_recomputes_the_campaign_status(
    client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
):
    from src.models import TestRun
    from tests.test_campaign_attempts import create_run, detail

    mock_server_service()
    await mock_git_token()
    stand_id, _ = await _create_stand(client, admin_token)
    await _create_test_def(client, admin_token, stand_id)
    run_id = await create_run(client, admin_token, [stand_id])
    data = await detail(client, admin_token, run_id)
    item = await _get_item(data["queue_items"][0]["queue_item_id"])

    resp = await client.post(f"{BASE}/{item.id}/skip", headers=auth_hdr(admin_token))
    assert resp.status_code == 200, resp.text
    assert (await _get_item(item.id)).state == QueueItemState.SKIPPED

    async with AsyncSessionLocal() as db:
        # Единственный элемент кампании терминален, но исхода не дал —
        # существующая агрегация кладёт это в partially_failed.
        assert (await db.get(TestRun, run_id)).status == RunStatus.PARTIALLY_FAILED
