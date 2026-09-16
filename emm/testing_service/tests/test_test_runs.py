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
from src.repositories import stp_composition as stp_composition_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.utils.ids import (
    department_test_settings_id,
    stp_cell_id,
    stp_composition_id as new_stp_composition_id,
    stp_test_case_id,
    stp_test_run_id,
)
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
from tests.test_stp import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    _create_test_def_for_dept,
    _seed_integration_settings,
    _seed_stp_test_case,
    mock_secret_client,
    mock_zephyr,
)

BASE = "/api/testing/v1/test-runs"
STANDS_BASE = "/api/testing/v1/test-stands"


def _payload(stand_ids: list[str], **overrides) -> dict:
    body = {
        "os_version_id": "osv_1.8.5",
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

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_a, stand_b], debug=True))
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

    async def test_campaign_can_mix_orel_and_smolensk_tests(
        self, client, admin_token, mock_server_service,
    ):
        """Кампания больше не несёт единый режим — у каждого теста свой (§ mode_switch)."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        orel_test = await _create_test_def(client, admin_token, stand_id, mode="orel")
        smolensk_test = await _create_test_def(client, admin_token, stand_id, mode="smolensk")

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id], debug=True))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["mode"] is None

        detail = await client.get(f"{BASE}/{body['id']}", headers=_hdr(admin_token))
        entries_by_test = {e["test_id"]: e for e in detail.json()["entries"]}
        assert entries_by_test[orel_test]["mode"] == "orel"
        assert entries_by_test[smolensk_test]["mode"] == "smolensk"

        modes_by_test = {}
        async with AsyncSessionLocal() as db:
            for i in detail.json()["queue_items"]:
                item = await queue_repo.get_by_id(db, i["queue_item_id"])
                modes_by_test[item.test_id] = item.launch_context["MODE"]
        assert modes_by_test[orel_test] == "orel"
        assert modes_by_test[smolensk_test] == "smolensk"

    async def test_stand_without_pinned_tests_does_not_break_campaign(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_with_test, _ = await _create_stand(client, admin_token)
        stand_without_test, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_with_test)

        resp = await client.post(
            BASE, headers=_hdr(admin_token), json=_payload([stand_with_test, stand_without_test], debug=True),
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

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([good_stand, bad_stand], debug=True))
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

    async def test_empty_stand_list_with_no_stp_composition_rejected(self, client, admin_token):
        """Пусто/не задано — путь вывода из СТП; без единой активной ячейки СТП запускать нечего."""
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([]))
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "STP_COMPOSITION_EMPTY"

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

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id], debug=True))
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

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_ok, stand_fail], debug=True))
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


class TestReleaseGateOnDebugNotFinal:
    """Допуск по СТП решает `debug`, а не `final` (§E1) — `final` теперь чисто
    информационная метка, не влияет на постановку в очередь."""

    async def test_non_debug_campaign_gates_even_when_not_final(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id], final=False))
        assert resp.status_code == 201, resp.text
        errors = resp.json()["enqueue_errors"]
        assert len(errors) == 1
        # СТП для этого контекста ни разу не генерировалась — случай (b) §E2.
        assert errors[0]["error_code"] == "STP_RUN_NOT_FOUND"

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
        assert errors[0]["error_code"] == "STP_RUN_NOT_FOUND"

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
                "mode": "orel", "kernel": payload["kernel"], "stand_id": stand_id,
                "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/stress_test",
            })
            await stp_cell_repo.create(db, {
                "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
            })
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert resp.status_code == 201, resp.text
        assert resp.json()["enqueue_errors"] == []

    async def test_skips_only_non_member_test_without_blocking_the_rest(
        self, client, admin_token, mock_server_service,
    ):
        """Из пула стенда только часть тестов в СТП — остальные запускаются, не-члены пропускаются (§E1)."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        member_id, member_code = await _create_test_def_with_known_code(client, admin_token, stand_id)
        other_id, _other_code = await _create_test_def_with_known_code(client, admin_token, stand_id)
        payload = _payload([stand_id], final=False)

        async with AsyncSessionLocal() as db:
            case = await stp_test_case_repo.create(db, {
                "id": stp_test_case_id(), "code": member_code, "title": member_code, "zephyr_id": "BT-T1",
            })
            run = await stp_test_run_repo.create(db, {
                "id": stp_test_run_id(), "os_version_id": payload["os_version_id"],
                "mode": "orel", "kernel": payload["kernel"], "stand_id": stand_id,
                "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/stress_test",
            })
            await stp_cell_repo.create(db, {
                "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
            })
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        errors = body["enqueue_errors"]
        assert len(errors) == 1
        assert errors[0]["test_id"] == other_id
        # run уже существует (для member_code), просто other_id в него не входит — случай (a).
        assert errors[0]["error_code"] == "TEST_NOT_IN_STP"

        detail = await client.get(f"{BASE}/{body['id']}", headers=_hdr(admin_token))
        items = detail.json()["queue_items"]
        assert len(items) == 1
        assert items[0]["test_id"] == member_id


class TestDebugCampaign:
    """`debug=True` — только вместе с явным `test_run_stands` (§E1); снимает и допуск
    по СТП, и требование готовности теста, весь пул стенда уходит в очередь как есть."""

    async def test_debug_requires_explicit_stands(self, client, admin_token, mock_server_service):
        mock_server_service()
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json={"os_version_id": "osv_1.8.5", "debug": True},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "TEST_RUN_DEBUG_REQUIRES_STANDS"

    async def test_debug_launches_everything_ignoring_readiness_and_stp(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        ready_id = await _create_test_def(client, admin_token, stand_id)
        broken_id = await _create_test_def(client, admin_token, stand_id)
        patch = await client.patch(
            f"{TESTS_BASE}/{broken_id}", headers=_hdr(admin_token), json={"readiness": "broken"},
        )
        assert patch.status_code == 200, patch.text

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id], debug=True))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["enqueue_errors"] == []

        detail = await client.get(f"{BASE}/{body['id']}", headers=_hdr(admin_token))
        items = detail.json()["queue_items"]
        assert {i["test_id"] for i in items} == {ready_id, broken_id}

        async with AsyncSessionLocal() as db:
            for i in items:
                item = await queue_repo.get_by_id(db, i["queue_item_id"])
                assert item.debug_mode is True
                assert item.stp_test_run_id is None


class TestFullScopeCampaign:
    """`full=True` — только без `test_run_stands`; синхронизирует СТП (`scope=full`)
    перед выводом состава, кампания собирается уже из расширенной СТП (§E1/E4)."""

    async def test_full_requires_no_explicit_stands(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id], full=True))
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "TEST_RUN_FULL_REQUIRES_STP_DERIVED"

    async def test_full_syncs_stp_then_launches_widened_composition(
        self, client, admin_token, mock_server_service, mock_zephyr, mock_secret_client, dept_a,
    ):
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        _test_id, code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)
        await _seed_stp_test_case(code, zephyr_id="BT-T1")
        mock_secret_client["cred_x"] = ("jira_bot", "tok123")
        await _seed_integration_settings(dept_a)

        # До синхронизации активного состава СТП для этого РЦ ещё нет вовсе.
        async with AsyncSessionLocal() as db:
            assert await stp_test_run_repo.list_by_department_and_os_version(db, dept_a, rc) == []

        resp = await client.post(
            BASE, headers=_hdr(admin_token), json={"os_version_id": rc, "kernel": "6.1.0", "full": True},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["composition_source"] == "stp_composition"
        assert body["stp_sync_errors"] == []
        assert body["enqueue_errors"] == []

        async with AsyncSessionLocal() as db:
            runs = await stp_test_run_repo.list_by_department_and_os_version(db, dept_a, rc)
        assert len(runs) == 1
        assert len(mock_zephyr["create"]) == 1

        detail = await client.get(f"{BASE}/{body['id']}", headers=_hdr(admin_token))
        entries = detail.json()["entries"]
        assert {e["test_code"] for e in entries} == {code}


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


class TestPreview:
    async def test_preview_reports_launch_when_stp_membership_exists(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        _test_id, code = await _create_test_def_with_known_code(client, admin_token, stand_id)
        payload = _payload([stand_id])

        async with AsyncSessionLocal() as db:
            case = await stp_test_case_repo.create(db, {
                "id": stp_test_case_id(), "code": code, "title": code, "zephyr_id": "BT-T1",
            })
            run = await stp_test_run_repo.create(db, {
                "id": stp_test_run_id(), "os_version_id": payload["os_version_id"],
                "mode": "orel", "kernel": payload["kernel"], "stand_id": stand_id,
                "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/stress_test",
            })
            await stp_cell_repo.create(db, {
                "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
            })
            await db.commit()

        resp = await client.post(f"{BASE}/preview", headers=_hdr(admin_token), json=payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stands_without_tests"] == []
        assert len(body["entries"]) == 1
        assert body["entries"][0]["action"] == "launch"
        assert body["entries"][0]["mode"] == "orel"

        listing = await client.get(BASE, headers=_hdr(admin_token), params={"department_id": "dep_a"})
        assert listing.json()["total"] == 0

    async def test_preview_reports_debug_required_for_non_ready_test(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        patch = await client.patch(
            f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token), json={"readiness": "broken"},
        )
        assert patch.status_code == 200, patch.text

        resp = await client.post(f"{BASE}/preview", headers=_hdr(admin_token), json=_payload([stand_id]))
        assert resp.status_code == 200, resp.text
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["action"] == "skip_debug_required"

    async def test_preview_reports_stp_not_generated_when_no_run_exists(self, client, admin_token, mock_server_service):
        """Случай (b) §E2: контекст никогда не синхронизировался с СТП — добавлять некуда."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(f"{BASE}/preview", headers=_hdr(admin_token), json=_payload([stand_id]))
        assert resp.status_code == 200, resp.text
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["action"] == "skip_stp_not_generated"
        assert entries[0]["stp_test_run_id"] is None

    async def test_preview_reports_not_in_stp_with_run_id_when_run_exists(
        self, client, admin_token, mock_server_service,
    ):
        """Случай (a) §E2: СТП для контекста уже есть (другой тест того же стенда),
        просто наш тест в неё не входит — `stp_test_run_id` указывает, куда добавлять."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        _member_id, member_code = await _create_test_def_with_known_code(client, admin_token, stand_id)
        await _create_test_def(client, admin_token, stand_id)
        payload = _payload([stand_id])

        async with AsyncSessionLocal() as db:
            case = await stp_test_case_repo.create(db, {
                "id": stp_test_case_id(), "code": member_code, "title": member_code, "zephyr_id": "BT-T1",
            })
            run = await stp_test_run_repo.create(db, {
                "id": stp_test_run_id(), "os_version_id": payload["os_version_id"],
                "mode": "orel", "kernel": payload["kernel"], "stand_id": stand_id,
                "zephyr_test_run_key": "BT-R1", "zephyr_folder_path": "/stress_test",
            })
            await stp_cell_repo.create(db, {
                "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
            })
            await db.commit()

        resp = await client.post(f"{BASE}/preview", headers=_hdr(admin_token), json=payload)
        assert resp.status_code == 200, resp.text
        entries = {e["test_code"]: e for e in resp.json()["entries"]}
        assert entries[member_code]["action"] == "launch"
        skipped = [e for e in entries.values() if e["test_code"] != member_code][0]
        assert skipped["action"] == "skip_not_in_stp"
        assert skipped["stp_test_run_id"] == run.id

    async def test_preview_debug_hides_stp_skip_reasons(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(f"{BASE}/preview", headers=_hdr(admin_token), json=_payload([stand_id], debug=True))
        assert resp.status_code == 200, resp.text
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["action"] == "launch"

    async def test_preview_debug_requires_explicit_stands(self, client, admin_token, mock_server_service):
        mock_server_service()
        resp = await client.post(
            f"{BASE}/preview", headers=_hdr(admin_token),
            json={"os_version_id": "osv_1.8.5", "debug": True},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "TEST_RUN_DEBUG_REQUIRES_STANDS"

    async def test_preview_full_requires_no_explicit_stands(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.post(f"{BASE}/preview", headers=_hdr(admin_token), json=_payload([stand_id], full=True))
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "TEST_RUN_FULL_REQUIRES_STP_DERIVED"

    async def test_preview_full_shows_would_launch_without_writing_to_db(
        self, client, admin_token, mock_server_service, dept_a,
    ):
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token, department_id=dept_a)
        test_id, _code = await _create_test_def_for_dept(client, admin_token, stand_id, dept_a)

        async with AsyncSessionLocal() as db:
            runs_before = await stp_test_run_repo.list_by_department_and_os_version(db, dept_a, rc)
        assert runs_before == []

        resp = await client.post(
            f"{BASE}/preview", headers=_hdr(admin_token), json={"os_version_id": rc, "kernel": "6.1.0", "full": True},
        )
        assert resp.status_code == 200, resp.text
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["test_id"] == test_id
        assert entries[0]["action"] == "launch"

        async with AsyncSessionLocal() as db:
            runs_after = await stp_test_run_repo.list_by_department_and_os_version(db, dept_a, rc)
        assert runs_after == []

        listing = await client.get(BASE, headers=_hdr(admin_token), params={"department_id": dept_a})
        runs_for_rc = [r for r in listing.json()["items"] if r["os_version_id"] == rc]
        assert runs_for_rc == []


async def _seed_stp_cell(
    db, *, stand_id: str, os_version_id: str, kernel: str, mode: str, code: str, is_active: bool = True,
):
    case = await stp_test_case_repo.create(db, {
        "id": stp_test_case_id(), "code": code, "title": code, "zephyr_id": f"BT-{code}",
    })
    run = await stp_test_run_repo.create(db, {
        "id": stp_test_run_id(), "os_version_id": os_version_id,
        "mode": mode, "kernel": kernel, "stand_id": stand_id,
        "zephyr_test_run_key": f"BT-R-{code}", "zephyr_folder_path": "/stress_test",
    })
    cell = await stp_cell_repo.create(db, {
        "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id, "is_active": is_active,
    })
    return case, run, cell


def _unique_rc() -> str:
    """`stp_compositions`/`stp_test_runs` не чистятся автоматической фикстурой между тестами
    (домен вне её ведения, см. `_cleanup_created_variables`) — каждый тест этого класса
    берёт свой РЦ, тем же приёмом, что и `tests/test_stp_composition.py`."""
    return f"osv_1.8.5.{uuid.uuid4().hex[:6]}"


class TestStpDerivedComposition:
    """`test_run_stands` не задан — полный прогон по РЦ, состав выводится из активной СТП (§B2/E1)."""

    async def test_derives_stands_tests_kernels_from_active_cells_only(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        rc = _unique_rc()
        stand_active, _ = await _create_stand(client, admin_token)
        stand_inactive_cell, _ = await _create_stand(client, admin_token)
        stand_no_stp, _ = await _create_stand(client, admin_token)
        _test_active_id, code_active = await _create_test_def_with_known_code(client, admin_token, stand_active)
        _test_inactive_id, code_inactive = await _create_test_def_with_known_code(client, admin_token, stand_inactive_cell)
        await _create_test_def_with_known_code(client, admin_token, stand_no_stp)

        async with AsyncSessionLocal() as db:
            await _seed_stp_cell(
                db, stand_id=stand_active, os_version_id=rc, kernel="6.1.0", mode="orel",
                code=code_active, is_active=True,
            )
            await _seed_stp_cell(
                db, stand_id=stand_inactive_cell, os_version_id=rc, kernel="6.1.0", mode="orel",
                code=code_inactive, is_active=False,
            )
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["test_run_stands"] == [stand_active]
        assert body["composition_source"] == "stp_composition"
        assert body["kernel"] == "6.1.0"

        detail = await client.get(f"{BASE}/{body['id']}", headers=_hdr(admin_token))
        entries = detail.json()["entries"]
        assert {e["test_code"] for e in entries} == {code_active}
        assert entries[0]["kernel"] == "6.1.0"
        assert entries[0]["mode"] == "orel"

    async def test_kernel_override_filters_derived_composition(self, client, admin_token, mock_server_service):
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token)
        _test_a_id, code_a = await _create_test_def_with_known_code(client, admin_token, stand_id)
        test_b_id, code_b = await _create_test_def_with_known_code(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await _seed_stp_cell(db, stand_id=stand_id, os_version_id=rc, kernel="6.1.0", mode="orel", code=code_a)
            await _seed_stp_cell(db, stand_id=stand_id, os_version_id=rc, kernel="6.2.0", mode="orel", code=code_b)
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json={"os_version_id": rc, "kernel": "6.2.0"})
        assert resp.status_code == 201, resp.text

        detail = await client.get(f"{BASE}/{resp.json()['id']}", headers=_hdr(admin_token))
        entries = detail.json()["entries"]
        assert {e["test_id"] for e in entries} == {test_b_id}
        assert entries[0]["kernel"] == "6.2.0"

    async def test_mode_mismatch_between_test_and_stp_run_prefers_test_mode(
        self, client, admin_token, mock_server_service,
    ):
        """Рассинхронизация данных (тест сменил режим после генерации СТП) — `test.mode` побеждает, не `StpTestRun.mode`."""
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token)
        _test_id, code = await _create_test_def_with_known_code(client, admin_token, stand_id)  # mode по умолчанию orel

        async with AsyncSessionLocal() as db:
            await _seed_stp_cell(db, stand_id=stand_id, os_version_id=rc, kernel="6.1.0", mode="smolensk", code=code)
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 201, resp.text

        detail = await client.get(f"{BASE}/{resp.json()['id']}", headers=_hdr(admin_token))
        entries = detail.json()["entries"]
        assert entries[0]["mode"] == "orel"

    async def test_records_stp_composition_snapshot(self, client, admin_token, mock_server_service):
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token)
        _test_id, code = await _create_test_def_with_known_code(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await _seed_stp_cell(db, stand_id=stand_id, os_version_id=rc, kernel="6.1.0", mode="orel", code=code)
            composition = await stp_composition_repo.create(db, {
                "id": new_stp_composition_id(), "department_id": "dep_a", "os_version_id": rc,
                "scope": "full", "revision": 3,
            })
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["stp_composition_id"] == composition.id
        assert body["stp_revision"] == 3

    async def test_no_stp_composition_row_leaves_snapshot_fields_null(self, client, admin_token, mock_server_service):
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token)
        _test_id, code = await _create_test_def_with_known_code(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await _seed_stp_cell(db, stand_id=stand_id, os_version_id=rc, kernel="6.1.0", mode="orel", code=code)
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["stp_composition_id"] is None
        assert body["stp_revision"] is None

    async def test_explicit_stands_path_ignores_stp_composition(self, client, admin_token, mock_server_service):
        """Явный `test_run_stands` — прежнее поведение без изменений, СТП не участвует, даже если её состав есть."""
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token)
        _test_id, code = await _create_test_def_with_known_code(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await _seed_stp_cell(db, stand_id=stand_id, os_version_id=rc, kernel="6.1.0", mode="orel", code=code)
            await stp_composition_repo.create(db, {
                "id": new_stp_composition_id(), "department_id": "dep_a", "os_version_id": rc,
                "scope": "full", "revision": 5,
            })
            await db.commit()

        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload([stand_id], os_version_id=rc))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["composition_source"] == "pinned_catalog"
        assert body["stp_composition_id"] is None
        assert body["stp_revision"] is None

    async def test_preview_derives_composition_too(self, client, admin_token, mock_server_service):
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id, code = await _create_test_def_with_known_code(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            await _seed_stp_cell(db, stand_id=stand_id, os_version_id=rc, kernel="6.1.0", mode="orel", code=code)
            await db.commit()

        resp = await client.post(f"{BASE}/preview", headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["entries"]) == 1
        assert body["entries"][0]["action"] == "launch"
        assert body["entries"][0]["test_id"] == test_id

        listing = await client.get(BASE, headers=_hdr(admin_token), params={"department_id": "dep_a"})
        runs_for_rc = [r for r in listing.json()["items"] if r["os_version_id"] == rc]
        assert runs_for_rc == []

    async def test_preview_empty_derived_composition_returns_empty_not_error(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        resp = await client.post(f"{BASE}/preview", headers=_hdr(admin_token), json={"os_version_id": "osv_nowhere"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["entries"] == []
        assert body["stands_without_tests"] == []
