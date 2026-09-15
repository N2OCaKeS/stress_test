"""Тесты `/api/testing/v1/test-runs` — прогоны (§2.4, §6.1 плана миграции).

Кампания разворачивается в независимые `queue_items` по каждому закреплённому
тесту каждого выбранного стенда через уже покрытый `services/queue.py`
(`test_queue.py`) — здесь проверяется именно fan-out кампании: стенд без
тестов не рушит остальное, частичный провал одного стенда не рушит остальные,
агрегатный статус пересчитывается по мере завершения дочерних item'ов, RBAC и
фильтры списка/карточки.

`server_client`-вызовы мокаются тем же `mock_server_service`, что и в
`test_queue.py` — фикстуры оттуда переиспользуются по имени.
"""

from __future__ import annotations

import uuid

from src.core.constants import QueueItemState
from src.db.session import AsyncSessionLocal
from src.repositories import department_test_settings as dts_repo
from src.repositories import queue_item as queue_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.utils.ids import department_test_settings_id, stp_cell_id, stp_test_case_id, stp_test_run_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    CALLBACK_BASE,
    QUEUE_BASE,
    TESTS_BASE,
    _create_stand,
    _create_test_def,
    _server_hdr,
    configure_internal_keys,
    mock_git_token,
    mock_server_service,
    recorded_calls,
    SERVER_SECRET,
    WORKER_SECRET,
)

BASE = "/api/testing/v1/test-runs"
STANDS_BASE = "/api/testing/v1/test-stands"


def _payload(stand_ids: list[str], **overrides) -> dict:
    body = {
        "os_version_id": "osv_1.8.5",
        "mode": "orel",
        "kernel": "6.1.0",
        "test_run_stands": stand_ids,
    }
    body.update(overrides)
    return body


async def _get_item(item_id: str):
    async with AsyncSessionLocal() as db:
        return await queue_repo.get_by_id(db, item_id)


async def _drive_to_success(client, item) -> None:
    """Прогнать один queue_item до `succeeded` через callback + claim + completed."""
    await client.post(
        f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
        headers=_server_hdr("server_service", SERVER_SECRET),
        json={
            "correlation_id": item.id, "succeeded": True,
            "test_username": "u", "test_password": "s3cr3t",
            "test_ssh_private_key": "-----KEY-----",
        },
    )
    resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
    claimed_id = resp.json()["item"]["queue_item_id"]
    await client.post(
        f"{QUEUE_BASE}/{claimed_id}/completed",
        headers=_server_hdr("testing_worker", WORKER_SECRET),
        json={"succeeded": True, "exit_code": 0},
    )


async def _drive_to_terminal_failure(client, item) -> None:
    """Прогнать один queue_item до окончательного `failed` (retry отключён на отделе)."""
    await client.post(
        f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
        headers=_server_hdr("server_service", SERVER_SECRET),
        json={"correlation_id": item.id, "succeeded": False, "failed_step": "restore", "error": "disk full"},
    )


class TestCreateTestRun:
    async def test_creates_queue_items_for_each_pinned_test(
        self, client, admin_token, mock_server_service, recorded_calls,
    ):
        mock_server_service()
        stand_a, _ = await _create_stand(client, admin_token)
        stand_b, _ = await _create_stand(client, admin_token)
        test_a = await _create_test_def(client, admin_token, stand_a)
        test_b = await _create_test_def(client, admin_token, stand_b)

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_a, stand_b]))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["stands_without_tests"] == []
        assert body["enqueue_errors"] == []
        assert body["status"] == "running"
        assert body["final"] is False

        detail = await client.get(f"{BASE}/{body['id']}", headers=_hdr(admin_token))
        assert detail.status_code == 200, detail.text
        items = detail.json()["queue_items"]
        assert {i["test_id"] for i in items} == {test_a, test_b}
        assert {i["stand_id"] for i in items} == {stand_a, stand_b}
        for i in items:
            assert i["state"] == "preparing"

        async with AsyncSessionLocal() as db:
            for i in items:
                item = await queue_repo.get_by_id(db, i["queue_item_id"])
                assert item.test_run_id == body["id"]
                assert item.launch_context["RC"] == "osv_1.8.5"
                assert item.launch_context["KERNEL"] == "6.1.0"
                assert item.launch_context["MODE"] == "orel"

    async def test_stand_without_pinned_tests_does_not_break_campaign(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_with_test, _ = await _create_stand(client, admin_token)
        stand_without_test, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_with_test)

        resp = await client.post(
            BASE, headers=_hdr(admin_token), json=_payload([stand_with_test, stand_without_test]),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["stands_without_tests"] == [stand_without_test]
        assert body["enqueue_errors"] == []

        detail = await client.get(f"{BASE}/{body['id']}", headers=_hdr(admin_token))
        items = detail.json()["queue_items"]
        assert len(items) == 1
        assert items[0]["stand_id"] == stand_with_test

    async def test_all_stands_without_tests_yields_queued_status(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id]))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["stands_without_tests"] == [stand_id]
        assert body["status"] == "queued"

    async def test_one_inactive_stand_does_not_break_the_others(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        good_stand, _ = await _create_stand(client, admin_token)
        bad_stand, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, good_stand)
        bad_test = await _create_test_def(client, admin_token, bad_stand)

        patch = await client.patch(
            f"{STANDS_BASE}/{bad_stand}", headers=_hdr(admin_token), json={"is_active": False},
        )
        assert patch.status_code == 200, patch.text

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([good_stand, bad_stand]))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["stands_without_tests"] == []
        assert len(body["enqueue_errors"]) == 1
        err = body["enqueue_errors"][0]
        assert err["stand_id"] == bad_stand
        assert err["test_id"] == bad_test
        assert err["error_code"] == "TEST_STAND_INACTIVE"

        detail = await client.get(f"{BASE}/{body['id']}", headers=_hdr(admin_token))
        items = detail.json()["queue_items"]
        assert len(items) == 1
        assert items[0]["stand_id"] == good_stand

    async def test_final_flag_persisted(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id], final=True))
        assert resp.status_code == 201, resp.text
        assert resp.json()["final"] is True

    async def test_no_role_gets_403(self, client, no_role_token, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.post(BASE, headers=_hdr(no_role_token), json=_payload([stand_id]))
        assert resp.status_code == 403, resp.text

    async def test_guest_cannot_create(self, client, guest_token, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.post(BASE, headers=_hdr(guest_token), json=_payload([stand_id]))
        assert resp.status_code == 403, resp.text

    async def test_missing_department_id_rejected(self, client, make_token, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        token = make_token(department_id=None, service_roles={"testing_service": ["admin"]})
        resp = await client.post(BASE, headers=_hdr(token), json=_payload([stand_id]))
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "TEST_RUN_DEPARTMENT_REQUIRED"

    async def test_empty_stand_list_rejected(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([]))
        assert resp.status_code == 422, resp.text

    async def test_requires_auth(self, client):
        resp = await client.post(BASE, json=_payload(["stand_x"]))
        assert resp.status_code == 401, resp.text


class TestAggregateStatus:
    async def test_status_succeeded_when_only_item_succeeds(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id]))
        run_id = resp.json()["id"]
        detail = await client.get(f"{BASE}/{run_id}", headers=_hdr(admin_token))
        item_id = detail.json()["queue_items"][0]["queue_item_id"]
        item = await _get_item(item_id)

        await _drive_to_success(client, item)

        final = await client.get(f"{BASE}/{run_id}", headers=_hdr(admin_token))
        assert final.json()["status"] == "succeeded"

    async def test_status_partially_failed_on_mixed_outcomes(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_ok, _ = await _create_stand(client, admin_token, department_id="dep_a")
        stand_fail, _ = await _create_stand(client, admin_token, department_id="dep_a")
        await _create_test_def(client, admin_token, stand_ok)
        await _create_test_def(client, admin_token, stand_fail)

        async with AsyncSessionLocal() as db:
            await dts_repo.create(db, {
                "id": department_test_settings_id(),
                "department_id": "dep_a",
                "retry_enabled": False,
                "test_username": "u",
                "activity_report_schedule": None,
            })
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_ok, stand_fail]))
        run_id = resp.json()["id"]
        detail = await client.get(f"{BASE}/{run_id}", headers=_hdr(admin_token))
        items = {i["stand_id"]: i["queue_item_id"] for i in detail.json()["queue_items"]}

        ok_item = await _get_item(items[stand_ok])
        fail_item = await _get_item(items[stand_fail])
        await _drive_to_success(client, ok_item)
        await _drive_to_terminal_failure(client, fail_item)

        final = await client.get(f"{BASE}/{run_id}", headers=_hdr(admin_token))
        assert final.json()["status"] == "partially_failed"
        states = {i["stand_id"]: i["state"] for i in final.json()["queue_items"]}
        assert states[stand_ok] == "succeeded"
        assert states[stand_fail] == "failed"


class TestListAndGet:
    async def test_filters_by_department_status_final(
        self, client, admin_token, make_token, mock_server_service,
    ):
        mock_server_service(department_id="dep_a")
        stand_a, _ = await _create_stand(client, admin_token, department_id="dep_a")
        await _create_test_def(client, admin_token, stand_a)
        run_a = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_a], final=True))
        run_a_id = run_a.json()["id"]

        token_b = make_token(department_id="dep_b", service_roles={"testing_service": ["admin"]})
        mock_server_service(department_id="dep_b")
        stand_b, _ = await _create_stand(client, token_b, department_id="dep_b")
        stand_b_no_test, _ = await _create_stand(client, token_b, department_id="dep_b")
        await _create_test_def(client, token_b, stand_b)
        run_b = await client.post(BASE, headers=_hdr(token_b), json=_payload([stand_b_no_test]))
        run_b_id = run_b.json()["id"]

        by_dept = await client.get(BASE, headers=_hdr(admin_token), params={"department_id": "dep_a"})
        assert by_dept.status_code == 200, by_dept.text
        ids = [r["id"] for r in by_dept.json()["items"]]
        assert run_a_id in ids
        assert run_b_id not in ids

        by_final = await client.get(BASE, headers=_hdr(admin_token), params={"final": True})
        ids = [r["id"] for r in by_final.json()["items"]]
        assert run_a_id in ids
        assert run_b_id not in ids

        by_status = await client.get(BASE, headers=_hdr(admin_token), params={"status": "queued"})
        ids = [r["id"] for r in by_status.json()["items"]]
        assert run_b_id in ids
        assert run_a_id not in ids

    async def test_get_unknown_id_404(self, client, admin_token):
        resp = await client.get(f"{BASE}/run_does_not_exist", headers=_hdr(admin_token))
        assert resp.status_code == 404, resp.text

    async def test_list_requires_auth(self, client):
        resp = await client.get(BASE)
        assert resp.status_code == 401, resp.text

    async def test_no_role_can_still_read(self, client, no_role_token, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)
        created = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id]))
        run_id = created.json()["id"]

        resp = await client.get(f"{BASE}/{run_id}", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text


async def _create_test_def_with_known_code(client, admin_token, pinned_stand_id: str) -> tuple[str, str]:
    code = f"run.stp.{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        TESTS_BASE, headers=_hdr(admin_token),
        json={"code": code, "full_name": "STP gate test", "readiness": "ready", "pinned_stand_id": pinned_stand_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], code


class TestFinalCampaignRequiresStp:
    """`final=True` — кампания официального релиза, должна соответствовать СТП (§E1)."""

    async def test_non_final_campaign_ignores_stp(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id]))
        assert resp.status_code == 201, resp.text
        assert resp.json()["enqueue_errors"] == []

    async def test_final_campaign_without_stp_membership_reports_partial_error(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id], final=True))
        assert resp.status_code == 201, resp.text
        errors = resp.json()["enqueue_errors"]
        assert len(errors) == 1
        assert errors[0]["error_code"] == "TEST_NOT_IN_STP"

    async def test_final_campaign_with_stp_membership_enqueues(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        _test_id, code = await _create_test_def_with_known_code(client, admin_token, stand_id)
        payload = _payload([stand_id], final=True)

        async with AsyncSessionLocal() as db:
            case = await stp_test_case_repo.create(db, {
                "id": stp_test_case_id(), "code": code, "title": code, "zephyr_id": "BT-T1",
            })
            run = await stp_test_run_repo.create(db, {
                "id": stp_test_run_id(), "os_version_id": payload["os_version_id"],
                "mode": payload["mode"], "kernel": payload["kernel"], "stand_id": stand_id,
                "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/stress_test",
            })
            await stp_cell_repo.create(db, {
                "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
            })
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert resp.status_code == 201, resp.text
        assert resp.json()["enqueue_errors"] == []


class TestRequestIdIdempotency:
    async def test_same_request_id_and_body_replays_without_duplicating(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)
        payload = _payload([stand_id], request_id="req_" + uuid.uuid4().hex)

        first = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert first.status_code == 201, first.text
        second = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert second.status_code == 201, second.text
        assert second.json()["id"] == first.json()["id"]

        listing = await client.get(BASE, headers=_hdr(admin_token), params={"department_id": "dep_a"})
        ids = [r["id"] for r in listing.json()["items"]]
        assert ids.count(first.json()["id"]) == 1

    async def test_same_request_id_different_body_conflicts(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_a, _ = await _create_stand(client, admin_token)
        stand_b, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_a)
        await _create_test_def(client, admin_token, stand_b)
        request_id = "req_" + uuid.uuid4().hex

        first = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_a], request_id=request_id))
        assert first.status_code == 201, first.text
        second = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_b], request_id=request_id))
        assert second.status_code == 409, second.text
        assert second.json()["error_code"] == "REQUEST_ID_CONFLICT"
