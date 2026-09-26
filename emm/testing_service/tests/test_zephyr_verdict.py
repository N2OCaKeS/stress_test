"""Вердикт теста из Zephyr, таблица маппинга статусов, `awaiting_verdict`.

Zephyr замокан на уровне `zephyr_client.get_test_run` (чтение статуса) и
`zephyr_client.update_test_result` (запись — её для `verdict_source=zephyr`
быть не должно). Опрос вызывается напрямую `queue.poll_awaiting_verdicts`
с подставленным `now` — так проверяются интервал опроса и таймаут T3 без
реального ожидания.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core.constants import QueueItemState, StpCellStatus
from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.repositories import queue_item as queue_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.services import queue as queue_svc
from src.services import secret_client, zephyr_client, zephyr_verdict
from src.services.zephyr_client import ZephyrTestRunDetail, ZephyrTestRunResultItem
from src.utils.ids import (
    department_integration_settings_id,
    stp_cell_id,
    stp_test_case_id,
    stp_test_run_id,
)
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    CALLBACK_BASE,
    LAUNCH_CTX,
    QUEUE_BASE,
    SERVER_SECRET,
    TESTS_BASE,
    WORKER_SECRET,
    _create_stand,
    _identity,
    _server_hdr,
    configure_internal_keys,  # noqa: F401 — фикстура
    mock_server_service,  # noqa: F401 — фикстура
    recorded_calls,  # noqa: F401 — зависимость mock_server_service
)

MAPPING_BASE = "/api/testing/v1/zephyr-status-mappings"
DTS_BASE = "/api/testing/v1/department-test-settings"
RUN_KEY = "BT-R7"
CASE_KEY = "BT-T7"


@pytest.fixture
def zephyr(monkeypatch):
    """Состояние замоканного Zephyr: статус кейса в прогоне, счётчик чтений,
    записи статусов, флаг недоступности."""
    state = {"status": None, "reads": 0, "pushed": [], "down": False, "case_key": CASE_KEY}

    async def fake_get_test_run(*, base_url, bearer_token, test_run_key, status_mapping=None):
        state["reads"] += 1
        if state["down"]:
            from src.core.exceptions import ServiceUnavailableError

            raise ServiceUnavailableError(error_code="ZEPHYR_UNREACHABLE", message="down")
        assert test_run_key == RUN_KEY
        assert bearer_token == "tok"
        return ZephyrTestRunDetail(key=RUN_KEY, name="run", folder=None, items=[
            ZephyrTestRunResultItem(
                test_case_key=state["case_key"], status="not_run",
                test_case_name="Verdict case", status_raw=state["status"],
            ),
            ZephyrTestRunResultItem(
                test_case_key="BT-OTHER", status="passed", test_case_name="other", status_raw="Pass",
            ),
        ])

    async def fake_update_result(*, base_url, bearer_token, test_run_key, test_case_key, status):
        state["pushed"].append((test_run_key, test_case_key, status))

    async def fake_reveal(cred_id):
        return ("jira_bot", "tok")

    monkeypatch.setattr(zephyr_client, "get_test_run", fake_get_test_run)
    monkeypatch.setattr(zephyr_client, "update_test_result", fake_update_result)
    monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
    return state


async def _create_test(client, admin_token, stand_id, department_id, *, verdict_source=None) -> tuple[str, str]:
    code = f"verdict.{uuid.uuid4().hex[:8]}"
    body = {
        "code": code, "full_name": "Verdict test", "department_id": department_id,
        "readiness": "ready", "pinned_stand_id": stand_id,
    }
    if verdict_source:
        body["verdict_source"] = verdict_source
    resp = await client.post(TESTS_BASE, headers=_hdr(admin_token), json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["verdict_source"] == (verdict_source or "zephyr")
    return resp.json()["id"], code


async def _seed_stp(stand_id: str, code: str, department_id: str, *, zephyr_run=True, case_key=CASE_KEY):
    async with AsyncSessionLocal() as db:
        case = await stp_test_case_repo.create(db, {
            "id": stp_test_case_id(), "code": code, "title": "Verdict case", "zephyr_id": case_key,
            "department_id": None, "created_by": "usr_test",
        })
        run = await stp_test_run_repo.create(db, {
            "id": stp_test_run_id(), "os_version_id": LAUNCH_CTX["RC"],
            "mode": LAUNCH_CTX["MODE"], "kernel": LAUNCH_CTX["KERNEL"],
            "stand_id": stand_id, "zephyr_test_run_key": RUN_KEY if zephyr_run else None,
            "zephyr_folder_path": "/stress_test/1.8/1.8.5",
        })
        cell = await stp_cell_repo.create(db, {
            "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
            "status": StpCellStatus.NOT_RUN,
        })
        await dis_repo.create(db, {
            "id": department_integration_settings_id(), "department_id": department_id,
            "credential_id": "cred_jira", "jira_base_url": "http://jira.example",
            "confluence_base_url": None, "bitbucket_credential_id": "cred_bitbucket",
        })
        await db.commit()
        return run.id, cell.id


async def _run_to_completion(
    client, department_id, test_id, *, stp_run_id=None, debug_stand_id=None, completed=None,
):
    """enqueue → prepare callback → claim → completed. Возвращает id item'а.

    `debug_stand_id` — debug-запуск на этом стенде (без СТП-прогона)."""
    async with AsyncSessionLocal() as db:
        item = await queue_svc.enqueue(
            db, _identity(department_id=department_id), test_id,
            launch_context=LAUNCH_CTX, debug_mode=debug_stand_id is not None,
            stand_id=debug_stand_id, stp_test_run_id=stp_run_id,
        )
    resp = await client.post(
        f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
        headers=_server_hdr("server_service", SERVER_SECRET),
        json={
            "correlation_id": item.id, "succeeded": True,
            "test_username": "u", "test_password": "s3cr3t", "test_ssh_private_key": "-----KEY-----",
        },
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"{QUEUE_BASE}/{item.id}/completed",
        headers=_server_hdr("testing_worker", WORKER_SECRET),
        json=completed or {"succeeded": True, "exit_code": 0},
    )
    assert resp.status_code == 200, resp.text
    return item.id


async def _item(item_id):
    async with AsyncSessionLocal() as db:
        return await queue_repo.get_by_id(db, item_id)


async def _poll(minutes: float = 0.0) -> int:
    async with AsyncSessionLocal() as db:
        return await queue_svc.poll_awaiting_verdicts(
            db, now=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        )


async def _setup(client, admin_token, mock_server_service, dept, *, zephyr_run=True, verdict_source=None):
    mock_server_service(department_id=dept)
    stand_id, _ = await _create_stand(client, admin_token, department_id=dept)
    test_id, code = await _create_test(client, admin_token, stand_id, dept, verdict_source=verdict_source)
    run_id, cell_id = await _seed_stp(stand_id, code, dept, zephyr_run=zephyr_run)
    return stand_id, test_id, run_id, cell_id


class TestVerdictFromZephyr:
    async def test_exit_zero_but_zephyr_fail_is_failed_with_retry(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, cell_id = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)

        item = await _item(item_id)
        assert item.state == QueueItemState.AWAITING_VERDICT, item.error
        assert item.verdict_source == "zephyr"
        assert item.verdict is None

        zephyr["status"] = "92"
        assert await _poll() == 1

        item = await _item(item_id)
        assert item.state == QueueItemState.FAILED
        assert item.verdict == "failed"
        assert item.failed_step == "verdict"
        assert item.zephyr_status_raw == "92"
        assert item.verdict_resolved_at is not None
        assert "92" in item.error
        async with AsyncSessionLocal() as db:
            cell = await stp_cell_repo.get_by_id(db, cell_id)
            retries = [i for i in await queue_repo.list_active_for_stand(db, item.stand_id) if i.retry_of_id == item_id]
        assert cell.status == StpCellStatus.FAIL
        # Статус в Zephyr выставил скрипт — сервис его не перезаписывает.
        assert zephyr["pushed"] == []
        # D13: повтор срабатывает и на fail от теста.
        assert len(retries) == 1

    async def test_zephyr_pass_is_succeeded(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, cell_id = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        zephyr["status"] = "91"
        assert await _poll() == 1

        item = await _item(item_id)
        assert item.state == QueueItemState.SUCCEEDED
        assert item.verdict == "passed"
        async with AsyncSessionLocal() as db:
            cell = await stp_cell_repo.get_by_id(db, cell_id)
        assert cell.status == StpCellStatus.PASSED
        assert zephyr["pushed"] == []

    async def test_atm_status_name_is_mapped(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        zephyr["status"] = "pass"  # регистр не важен
        assert await _poll() == 1
        assert (await _item(item_id)).state == QueueItemState.SUCCEEDED

    async def test_in_progress_until_timeout_fails_with_t3_reason(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        zephyr["status"] = "90"

        assert await _poll() == 0
        assert zephyr["reads"] == 1
        # Интервал опроса (60 с) не прошёл — Zephyr не дёргаем.
        assert await _poll(minutes=0.5) == 0
        assert zephyr["reads"] == 1
        assert await _poll(minutes=2) == 0
        assert zephyr["reads"] == 2
        item = await _item(item_id)
        assert item.state == QueueItemState.AWAITING_VERDICT
        assert item.zephyr_status_raw == "90"

        # 35 минут по умолчанию (T3).
        assert await _poll(minutes=36) == 1
        item = await _item(item_id)
        assert item.state == QueueItemState.FAILED
        assert item.verdict == "failed"
        assert "не выставил итоговый статус" in item.error
        assert "90" in item.error
        assert zephyr["pushed"] == []

    async def test_zephyr_unreachable_keeps_waiting_then_times_out(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        zephyr["down"] = True
        assert await _poll() == 0
        assert (await _item(item_id)).state == QueueItemState.AWAITING_VERDICT
        assert await _poll(minutes=40) == 1
        item = await _item(item_id)
        assert item.state == QueueItemState.FAILED
        assert "Zephyr недоступен" in item.error

    async def test_unfinished_outcome_setting(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        resp = await client.put(
            f"{DTS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"zephyr_verdict_wait_seconds": 60, "zephyr_verdict_unfinished_outcome": "passed"},
        )
        assert resp.status_code == 200, resp.text
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        zephyr["status"] = "Not Executed"
        assert await _poll(minutes=2) == 1
        item = await _item(item_id)
        assert item.state == QueueItemState.SUCCEEDED
        assert item.verdict == "passed"

    async def test_stand_held_until_verdict(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        stand_id, test_id, run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        first_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        async with AsyncSessionLocal() as db:
            second = await queue_svc.enqueue(
                db, _identity(department_id=dept_a), test_id,
                launch_context=LAUNCH_CTX, stp_test_run_id=run_id, on_active_queue="append",
            )
        assert (await _item(second.id)).state == QueueItemState.QUEUED

        # Удалить ждущий вердикта item нельзя — он держит стенд.
        resp = await client.delete(f"/api/testing/v1/queue-items/{first_id}", headers=_hdr(admin_token))
        assert resp.status_code in (404, 405, 409), resp.text

        zephyr["status"] = "Pass"
        assert await _poll() == 1
        assert (await _item(first_id)).state == QueueItemState.SUCCEEDED
        assert (await _item(second.id)).state == QueueItemState.PREPARING

    async def test_duplicate_completion_while_awaiting_is_noop(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        resp = await client.post(
            f"{QUEUE_BASE}/{item_id}/completed",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"succeeded": False, "exit_code": 1},
        )
        assert resp.status_code == 200
        assert (await _item(item_id)).state == QueueItemState.AWAITING_VERDICT

    async def test_nonzero_exit_fails_without_waiting(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(
            client, dept_a, test_id, stp_run_id=run_id,
            completed={"succeeded": False, "exit_code": 2, "error": "git clone failed"},
        )
        item = await _item(item_id)
        assert item.state == QueueItemState.FAILED
        assert item.verdict == "failed"
        assert item.verdict_source == "exit_code"
        assert zephyr["reads"] == 0

    async def test_exit_code_source_keeps_old_behaviour(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, cell_id = await _setup(
            client, admin_token, mock_server_service, dept_a, verdict_source="exit_code",
        )
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        item = await _item(item_id)
        assert item.state == QueueItemState.SUCCEEDED
        assert item.verdict == "passed"
        assert item.verdict_source == "exit_code"
        assert zephyr["reads"] == 0
        # Для exit_code сервис по-прежнему сам пишет статус в Zephyr.
        assert zephyr["pushed"] == [(RUN_KEY, CASE_KEY, StpCellStatus.PASSED)]


class TestVerdictWithoutZephyrRun:
    async def test_debug_run_gets_unknown_verdict(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        stand_id, test_id, _run_id, cell_id = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(client, dept_a, test_id, debug_stand_id=stand_id)
        item = await _item(item_id)
        assert item.state == QueueItemState.SUCCEEDED
        assert item.verdict == "unknown"
        assert item.verdict_source is None
        assert zephyr["reads"] == 0
        async with AsyncSessionLocal() as db:
            cell = await stp_cell_repo.get_by_id(db, cell_id)
        assert cell.status == StpCellStatus.NOT_RUN
        assert zephyr["pushed"] == []

        resp = await client.get(
            "/api/testing/v1/queue-items", headers=_hdr(admin_token), params={"stand_id": item.stand_id},
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()["items"] if isinstance(resp.json(), dict) else resp.json()
        row = next(r for r in rows if r["id"] == item_id)
        assert row["verdict"] == "unknown"

    async def test_run_without_zephyr_key_gets_unknown(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, _cell = await _setup(
            client, admin_token, mock_server_service, dept_a, zephyr_run=False,
        )
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        item = await _item(item_id)
        assert item.state == QueueItemState.SUCCEEDED
        assert item.verdict == "unknown"

    async def test_exit_code_setting_for_debug(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        stand_id, test_id, _run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        resp = await client.put(
            f"{DTS_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"verdict_without_zephyr_run": "exit_code"},
        )
        assert resp.status_code == 200, resp.text
        item_id = await _run_to_completion(client, dept_a, test_id, debug_stand_id=stand_id)
        item = await _item(item_id)
        assert item.state == QueueItemState.SUCCEEDED
        assert item.verdict == "passed"
        assert item.verdict_source == "exit_code"


class TestSkipWhileAwaiting:
    async def test_skip_awaiting_item_frees_stand(
        self, client, admin_token, mock_server_service, configure_internal_keys, zephyr, dept_a,
    ):
        _stand, test_id, run_id, _cell = await _setup(client, admin_token, mock_server_service, dept_a)
        item_id = await _run_to_completion(client, dept_a, test_id, stp_run_id=run_id)
        async with AsyncSessionLocal() as db:
            item = await queue_repo.get_by_id(db, item_id)
            from src.repositories import test_stand as stand_repo

            stand = await stand_repo.get_by_id(db, item.stand_id)
            await queue_svc.request_interrupt(db, item, stand, "skip")
        assert (await _item(item_id)).state == QueueItemState.SKIPPED
        # Опрос его больше не видит.
        zephyr["status"] = "Fail"
        assert await _poll() == 0


class TestStatusMappingApi:
    async def test_default_then_custom_then_reset(self, client, admin_token, dept_a):
        resp = await client.get(f"{MAPPING_BASE}/{dept_a}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_default"] is True
        by_status = {i["zephyr_status"]: i["outcome"] for i in body["items"]}
        assert by_status["Pass"] == "passed"
        assert by_status["92"] == "failed"
        assert by_status["90"] == "not_finished"
        assert by_status["89"] == "not_finished"

        resp = await client.put(
            f"{MAPPING_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"items": [
                {"zephyr_status": "Pass", "outcome": "passed"},
                {"zephyr_status": " Blocked ", "outcome": "failed"},
            ]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_default"] is False
        async with AsyncSessionLocal() as db:
            mapping = await zephyr_verdict.load_mapping(db, dept_a)
        assert zephyr_verdict.classify("blocked", mapping) == "failed"
        # Своих строк у отдела хватает — набор по умолчанию не подмешивается.
        assert zephyr_verdict.classify("92", mapping) == "not_finished"

        resp = await client.delete(f"{MAPPING_BASE}/{dept_a}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_default"] is True

    async def test_duplicate_statuses_rejected(self, client, admin_token, dept_a):
        resp = await client.put(
            f"{MAPPING_BASE}/{dept_a}", headers=_hdr(admin_token),
            json={"items": [
                {"zephyr_status": "Pass", "outcome": "passed"},
                {"zephyr_status": "pass", "outcome": "failed"},
            ]},
        )
        assert resp.status_code == 422

    async def test_guest_cannot_update(self, client, guest_token, dept_a):
        resp = await client.put(
            f"{MAPPING_BASE}/{dept_a}", headers=_hdr(guest_token),
            json={"items": [{"zephyr_status": "Pass", "outcome": "passed"}]},
        )
        assert resp.status_code == 403

    async def test_other_department_cannot_read(self, client, admin_token):
        resp = await client.get(f"{MAPPING_BASE}/dep_other", headers=_hdr(admin_token))
        assert resp.status_code == 403

    async def test_settings_defaults(self, client, admin_token, dept_a):
        resp = await client.get(f"{DTS_BASE}/{dept_a}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["zephyr_verdict_wait_seconds"] == 2100
        assert body["zephyr_verdict_poll_seconds"] == 60
        assert body["zephyr_verdict_unfinished_outcome"] == "failed"
        assert body["verdict_without_zephyr_run"] == "unknown"


class TestClassify:
    def test_unknown_or_empty_is_not_finished(self):
        mapping = {"pass": "passed"}
        assert zephyr_verdict.classify(None, mapping) == "not_finished"
        assert zephyr_verdict.classify("", mapping) == "not_finished"
        assert zephyr_verdict.classify("Weird", mapping) == "not_finished"
        assert zephyr_verdict.classify(" PASS ", mapping) == "passed"

    def test_pull_cell_status_uses_mapping(self):
        mapping = {"blocked": "failed", "in progress": "not_finished"}
        assert zephyr_client.map_status_from_zephyr("Blocked", mapping) == StpCellStatus.FAIL
        assert zephyr_client.map_status_from_zephyr("In Progress", mapping) == StpCellStatus.IN_PROGRESS
        assert zephyr_client.map_status_from_zephyr("Pass", mapping) == StpCellStatus.NOT_RUN
        assert zephyr_client.map_status_from_zephyr("Pass") == StpCellStatus.PASSED
