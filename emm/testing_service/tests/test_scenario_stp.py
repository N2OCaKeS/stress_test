"""Сценарий как механизм запуска тест-кейса СТП.

* в ячейку СТП пишет только действие-вердикт сценария;
* запуск сценария с `stp_test_run_id` (клик по ячейке) пишет вердикт в эту
  ячейку и допускается так же, как обычный запуск по СТП;
* кампания по СТП запускает `ready`-сценарий вместо одиночного теста, её
  статус считается по исходу запуска сценария.
"""

from __future__ import annotations

import uuid

import pytest

from src.db.session import AsyncSessionLocal
from src.models import QueueItem, Scenario, ScenarioRun, StpCell
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.utils.ids import stp_cell_id, stp_test_case_id, stp_test_run_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import LAUNCH_CTX, _create_stand
from tests.test_scenario_runs import (  # noqa: F401 — фикстуры
    SCENARIOS,
    _claim,
    _complete,
    _exit_code_test,
    _get,
    _prepared,
    configure_internal_keys,
    mock_git_token,
    mock_server_service,
    recorded_calls,
    stands_api,
)

TEST_RUNS = "/api/testing/v1/test-runs"
QUEUE_ITEMS = "/api/testing/v1/queue-items"
KERNEL = LAUNCH_CTX["KERNEL"]


def _rc() -> str:
    return f"osv_scn.{uuid.uuid4().hex[:6]}"


async def _case(db, code: str):
    case = await stp_test_case_repo.get_by_code(db, code)
    if case is None:
        case = await stp_test_case_repo.create(db, {
            "id": stp_test_case_id(), "code": code, "title": code, "zephyr_id": f"BT-{code}",
        })
    return case


async def _stp_cell(*, stand_id: str, rc: str, code: str, kernel: str = KERNEL, mode: str = "orel") -> tuple[str, str]:
    """Столбец СТП `(stand, rc, kernel, mode)` + активная ячейка кейса `code`; → (run_id, cell_id)."""
    async with AsyncSessionLocal() as db:
        case = await _case(db, code)
        run = await stp_test_run_repo.find_latest_for_context(
            db, stand_id=stand_id, os_version_id=rc, mode=mode, kernel=kernel,
        )
        if run is None:
            run = await stp_test_run_repo.create(db, {
                "id": stp_test_run_id(), "os_version_id": rc, "mode": mode, "kernel": kernel,
                "stand_id": stand_id, "zephyr_test_run_key": None, "zephyr_folder_path": "/stress_test",
            })
        cell = await stp_cell_repo.create(db, {
            "id": stp_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id, "is_active": True,
        })
        await db.commit()
        return run.id, cell.id


async def _cell(cell_id: str) -> StpCell:
    async with AsyncSessionLocal() as db:
        return await db.get(StpCell, cell_id)


async def _test_code(client, token, test_id: str) -> str:
    resp = await client.get(f"/api/testing/v1/test-definitions/{test_id}", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["code"]


async def _stp_scenario(client, token, *, readiness: str = "ready") -> dict:
    """КД (`full`, вердикт) + клиент (`none`, подготовительное действие без вердикта)."""
    (kd, kd_srv), (cl, _cl_srv) = await _create_stand(client, token), await _create_stand(client, token)
    setup_test, verdict_test = await _exit_code_test(client, token), await _exit_code_test(client, token)
    case_code = f"scn.case.{uuid.uuid4().hex[:8]}"
    resp = await client.post(SCENARIOS, headers=_hdr(token), json={
        "code": f"scn.{uuid.uuid4().hex[:8]}", "name": "КД + клиент", "department_id": "dep_a",
        "readiness": readiness, "stp_test_case_code": case_code,
        "stands": [
            {"stand_id": kd, "label": "КД", "preparation": "full"},
            {"stand_id": cl, "label": "клиент", "preparation": "none"},
        ],
        "actions": [
            {"kind": "run_test", "stand_id": cl, "test_id": setup_test},
            {"kind": "run_test", "stand_id": kd, "test_id": verdict_test, "is_verdict": True},
        ],
    })
    assert resp.status_code == 201, resp.text
    return {
        "scenario": resp.json(), "kd": kd, "kd_srv": kd_srv, "cl": cl, "case_code": case_code,
        "setup_test": setup_test, "verdict_test": verdict_test,
    }


async def _drive(client, stands_api, run_id: str, *, verdict_ok: bool = True) -> tuple[str, str]:
    """Подготовка КД → действие клиента → действие-вердикт; → (setup item, verdict item)."""
    await _prepared(client, stands_api["prepare"][-1]["prepare_request_id"])
    setup = await _claim(client)
    await _complete(client, setup["queue_item_id"])
    verdict = await _claim(client)
    await _complete(
        client, verdict["queue_item_id"],
        **({} if verdict_ok else {"succeeded": False, "exit_code": 3}),
    )
    return setup["queue_item_id"], verdict["queue_item_id"]


class TestScenarioStpLink:
    async def test_put_without_field_keeps_link_and_code_is_unique_in_department(
        self, client, admin_token, stands_api,
    ):
        s = await _stp_scenario(client, admin_token)
        scn = s["scenario"]
        assert scn["stp_test_case_code"] == s["case_code"]
        body = {k: scn[k] for k in ("code", "name", "department_id", "readiness")}
        body["stands"] = [{k: v for k, v in r.items() if k not in ("id", "target_type", "stand_name")}
                          for r in scn["stands"]]
        body["actions"] = [{k: v for k, v in a.items() if k not in ("id", "position", "test_code")}
                           for a in scn["actions"]]
        resp = await client.put(f"{SCENARIOS}/{scn['id']}", headers=_hdr(admin_token), json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["stp_test_case_code"] == s["case_code"]

        other = await _stp_scenario(client, admin_token)
        taken = {**body, "code": other["scenario"]["code"], "stp_test_case_code": s["case_code"]}
        taken["stands"] = [{k: v for k, v in r.items() if k not in ("id", "target_type", "stand_name")}
                           for r in other["scenario"]["stands"]]
        taken["actions"] = [{k: v for k, v in a.items() if k not in ("id", "position", "test_code")}
                            for a in other["scenario"]["actions"]]
        resp = await client.put(f"{SCENARIOS}/{other['scenario']['id']}", headers=_hdr(admin_token), json=taken)
        assert resp.status_code == 409 and resp.json()["error_code"] == "SCENARIO_STP_CASE_TAKEN"

        # Кейс СТП требует однозначного вердикта.
        taken["stp_test_case_code"] = f"scn.case.{uuid.uuid4().hex[:8]}"
        taken["actions"][0]["is_verdict"] = True
        resp = await client.put(f"{SCENARIOS}/{other['scenario']['id']}", headers=_hdr(admin_token), json=taken)
        assert resp.status_code == 422 and resp.json()["error_code"] == "SCENARIO_STP_VERDICT_AMBIGUOUS"


class TestManualScenarioLaunchFromStp:
    async def test_only_verdict_action_updates_its_stp_cell(
        self, client, admin_token, stands_api, configure_internal_keys, mock_git_token,
    ):
        """Ручной запуск из ячейки СТП: вердикт пишется в ячейку кейса сценария,
        исход подготовительного действия не трогает ячейку его собственного теста."""
        await mock_git_token()
        s = await _stp_scenario(client, admin_token)
        rc = _rc()
        stp_run, verdict_cell = await _stp_cell(stand_id=s["kd"], rc=rc, code=s["case_code"])
        # Ячейка теста подготовительного действия в том же контексте — до
        # фильтра по `is_verdict` она обновлялась бы исходом действия.
        _run, setup_cell = await _stp_cell(
            stand_id=s["cl"], rc=rc, code=await _test_code(client, admin_token, s["setup_test"]),
        )
        before = (await _cell(setup_cell)).status

        resp = await client.post(
            f"{SCENARIOS}/{s['scenario']['id']}/runs", headers=_hdr(admin_token),
            json={"os_version_id": rc, "kernel": KERNEL, "mode": "orel", "stp_test_run_id": stp_run},
        )
        assert resp.status_code == 201, resp.text
        run = resp.json()
        assert run["stp_test_run_id"] == stp_run

        setup_item, verdict_item = await _drive(client, stands_api, run["id"])
        assert (await _get(client, admin_token, run["id"]))["state"] == "succeeded"
        async with AsyncSessionLocal() as db:
            assert (await db.get(QueueItem, verdict_item)).stp_test_run_id == stp_run
            assert (await db.get(QueueItem, setup_item)).stp_test_run_id is None

        cell = await _cell(verdict_cell)
        assert cell.status == "pass" and cell.queue_item_id == verdict_item
        untouched = await _cell(setup_cell)
        assert untouched.status == before and untouched.queue_item_id is None

    async def test_failed_verdict_marks_cell_fail(
        self, client, admin_token, stands_api, configure_internal_keys, mock_git_token,
    ):
        await mock_git_token()
        s = await _stp_scenario(client, admin_token)
        rc = _rc()
        stp_run, verdict_cell = await _stp_cell(stand_id=s["kd"], rc=rc, code=s["case_code"])
        resp = await client.post(
            f"{SCENARIOS}/{s['scenario']['id']}/runs", headers=_hdr(admin_token),
            json={"os_version_id": rc, "kernel": KERNEL, "mode": "orel", "stp_test_run_id": stp_run},
        )
        assert resp.status_code == 201, resp.text
        _setup, verdict_item = await _drive(client, stands_api, resp.json()["id"], verdict_ok=False)
        assert (await _cell(verdict_cell)).status == "fail"

        # Одиночный retry действия-вердикта записал бы в ячейку исход запуска
        # без второго стенда — повторяется только сценарий целиком.
        resp = await client.post(
            f"{QUEUE_ITEMS}/{verdict_item}/retry", headers=_hdr(admin_token),
            json={"request_id": f"req-{uuid.uuid4().hex}"},
        )
        assert resp.status_code == 409 and resp.json()["error_code"] == "RETRY_NOT_ALLOWED"
        resp = await client.post(f"/api/testing/v1/test-stands/{s['kd']}/retry-failed", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["skipped_count"] == 1 and resp.json()["retried_count"] == 0

    async def test_stp_launch_is_gated_like_a_plain_stp_launch(
        self, client, admin_token, make_token, stands_api,
    ):
        """Без права запуска на стенд и без членства кейса в столбце СТП — те же
        отказы, что у обычного запуска теста по СТП."""
        s = await _stp_scenario(client, admin_token)
        rc = _rc()
        stp_run, _cell_id = await _stp_cell(stand_id=s["kd"], rc=rc, code=s["case_code"])
        # Столбец СТП, где кейса нет, и тест, которого нет в нём же.
        empty_run, _ = await _stp_cell(stand_id=s["cl"], rc=rc, code=f"other.{uuid.uuid4().hex[:6]}")
        body = {"os_version_id": rc, "kernel": KERNEL, "mode": "orel"}

        guest = make_token(department_id="dep_a", service_roles={"testing_service": ["guest"]})
        plain = await client.post(QUEUE_ITEMS, headers=_hdr(guest), json={
            "test_id": s["verdict_test"], "stand_id": s["kd"], "os_version_id": rc, "kernel": KERNEL,
            "request_id": f"req-{uuid.uuid4().hex}",
        })
        scenario = await client.post(
            f"{SCENARIOS}/{s['scenario']['id']}/runs", headers=_hdr(guest), json={**body, "stp_test_run_id": stp_run},
        )
        assert plain.status_code == scenario.status_code == 403
        assert plain.json()["error_code"] == scenario.json()["error_code"]

        # Столбец СТП другого стенда — для стенда КД СТП «нет», как у одиночного теста.
        resp = await client.post(
            f"{SCENARIOS}/{s['scenario']['id']}/runs", headers=_hdr(admin_token),
            json={**body, "stp_test_run_id": empty_run},
        )
        assert resp.status_code == 422 and resp.json()["error_code"] == "STP_RUN_NOT_FOUND"
        # Кейс не в составе столбца КД.
        other_rc = _rc()
        kd_run, _ = await _stp_cell(stand_id=s["kd"], rc=other_rc, code=f"other.{uuid.uuid4().hex[:6]}")
        resp = await client.post(
            f"{SCENARIOS}/{s['scenario']['id']}/runs", headers=_hdr(admin_token),
            json={**body, "os_version_id": other_rc, "stp_test_run_id": kd_run},
        )
        assert resp.status_code == 422 and resp.json()["error_code"] == "TEST_NOT_IN_STP"
        assert resp.json()["details"]["stp_test_run_id"] == kd_run

        resp = await client.post(
            f"{SCENARIOS}/{s['scenario']['id']}/runs", headers=_hdr(admin_token),
            json={**body, "stp_test_run_id": stp_run, "debug": True},
        )
        assert resp.status_code == 422 and resp.json()["error_code"] == "SCENARIO_STP_DEBUG_CONFLICT"
        async with AsyncSessionLocal() as db:
            runs = (await db.execute(
                ScenarioRun.__table__.select().where(ScenarioRun.scenario_id == s["scenario"]["id"])
            )).all()
        assert runs == []


class TestCampaignRoutesToScenario:
    @pytest.mark.parametrize("verdict_ok,expected", [(True, "succeeded"), (False, "failed")])
    async def test_campaign_starts_scenario_and_takes_its_outcome(
        self, client, admin_token, stands_api, configure_internal_keys, mock_git_token, verdict_ok, expected,
    ):
        await mock_git_token()
        s = await _stp_scenario(client, admin_token)
        rc = _rc()
        stp_run, verdict_cell = await _stp_cell(stand_id=s["kd"], rc=rc, code=s["case_code"])

        preview = await client.post(f"{TEST_RUNS}/preview", headers=_hdr(admin_token), json={"os_version_id": rc})
        assert preview.status_code == 200, preview.text
        [entry] = preview.json()["entries"]
        assert entry["action"] == "launch" and entry["scenario_id"] == s["scenario"]["id"]
        # Кейс без одноимённого теста — запись несёт тест действия-вердикта.
        assert entry["test_id"] == s["verdict_test"]

        resp = await client.post(TEST_RUNS, headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 201, resp.text
        campaign = resp.json()
        assert campaign["enqueue_errors"] == [] and campaign["status"] == "running"

        detail = (await client.get(f"{TEST_RUNS}/{campaign['id']}", headers=_hdr(admin_token))).json()
        assert detail["queue_items"] == []  # одиночной постановки нет
        [entry] = detail["entries"]
        run_id = entry["scenario_run_id"]
        run = await _get(client, admin_token, run_id)
        assert run["test_run_id"] == campaign["id"] and run["stp_test_run_id"] == stp_run
        assert run["state"] == "preparing"

        await _drive(client, stands_api, run_id, verdict_ok=verdict_ok)
        detail = (await client.get(f"{TEST_RUNS}/{campaign['id']}", headers=_hdr(admin_token))).json()
        assert detail["status"] == expected
        assert detail["progress"]["total"] == 1
        assert (await _cell(verdict_cell)).status == ("pass" if verdict_ok else "fail")

    async def test_not_ready_scenario_leaves_plain_enqueue_and_mixed_campaign_does_both(
        self, client, admin_token, stands_api,
    ):
        ready = await _stp_scenario(client, admin_token)
        draft = await _stp_scenario(client, admin_token, readiness="development")
        rc = _rc()
        await _stp_cell(stand_id=ready["kd"], rc=rc, code=ready["case_code"])
        # Кейс черновика совпадает с кодом его теста — без сценария это обычный тест.
        draft_code = await _test_code(client, admin_token, draft["verdict_test"])
        async with AsyncSessionLocal() as db:
            row = await db.get(Scenario, draft["scenario"]["id"])
            row.stp_test_case_code = draft_code
            await db.commit()
        await _stp_cell(stand_id=draft["kd"], rc=rc, code=draft_code)
        resp = await client.patch(
            f"/api/testing/v1/test-definitions/{draft['verdict_test']}", headers=_hdr(admin_token),
            json={"pinned_stand_id": draft["kd"]},
        )
        assert resp.status_code == 200, resp.text

        preview = (await client.post(f"{TEST_RUNS}/preview", headers=_hdr(admin_token), json={"os_version_id": rc})).json()
        by_test = {e["test_id"]: e for e in preview["entries"]}
        assert by_test[ready["verdict_test"]]["scenario_id"] == ready["scenario"]["id"]
        assert by_test[draft["verdict_test"]]["scenario_id"] is None

        resp = await client.post(TEST_RUNS, headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 201, resp.text
        detail = (await client.get(f"{TEST_RUNS}/{resp.json()['id']}", headers=_hdr(admin_token))).json()
        assert [i["test_id"] for i in detail["queue_items"]] == [draft["verdict_test"]]
        entries = {e["test_id"]: e for e in detail["entries"]}
        assert entries[ready["verdict_test"]]["scenario_run_id"] is not None
        assert entries[draft["verdict_test"]]["scenario_run_id"] is None

    async def test_campaign_scenario_is_gated_by_stp_membership_of_dc_stand(
        self, client, admin_token, stands_api,
    ):
        """Столбец СТП кейса — на стенде клиента, а вердикт — на КД: запуск
        сценария отклоняется тем же `STP_RUN_NOT_FOUND`, что и одиночный тест."""
        s = await _stp_scenario(client, admin_token)
        rc = _rc()
        await _stp_cell(stand_id=s["cl"], rc=rc, code=s["case_code"])
        resp = await client.post(TEST_RUNS, headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert [e["error_code"] for e in body["enqueue_errors"]] == ["STP_RUN_NOT_FOUND"]
        assert body["status"] == "failed"
        async with AsyncSessionLocal() as db:
            runs = (await db.execute(
                ScenarioRun.__table__.select().where(ScenarioRun.scenario_id == s["scenario"]["id"])
            )).all()
        assert runs == []
