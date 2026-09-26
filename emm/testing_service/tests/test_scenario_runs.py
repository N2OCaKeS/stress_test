"""Запуск многостендового сценария — бронь «всё или ничего», подготовка,
действия по порядку, освобождение, взаимодействие с одностендовой очередью.

server_service подменён на уровне `server_client` (бронь, подготовка,
освобождение — по цели стенда), воркер — вызовами `/internal/queue`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core.constants import QueueItemState
from src.db.session import AsyncSessionLocal
from src.models import QueueItem, ScenarioRun
from src.services import queue as queue_svc
from src.services import scenario_queue
from src.services import server_client
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    CALLBACK_BASE,
    LAUNCH_CTX,
    QUEUE_BASE,
    SERVER_SECRET,
    TESTS_BASE,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _identity,
    _server_hdr,
    configure_internal_keys,  # noqa: F401 — фикстура
    mock_git_token,  # noqa: F401 — фикстура
    mock_server_service,  # noqa: F401 — фикстура
    recorded_calls,  # noqa: F401 — зависимость mock_server_service
)

SCENARIOS = "/api/testing/v1/scenarios"
RUNS = "/api/testing/v1/scenario-runs"


@pytest.fixture
def stands_api(monkeypatch, mock_server_service):
    """Бронь/подготовка/освобождение по цели стенда; `busy` — server_id, которые не отдаются."""
    mock_server_service()
    calls: dict = {"acquire": [], "release": [], "release_done": [], "prepare": [], "busy": set()}

    async def acquire(target, **kwargs):
        calls["acquire"].append(target.id)
        if target.id in calls["busy"]:
            from src.core.exceptions import ConflictError
            raise ConflictError(error_code="SERVER_ALREADY_BUSY", message="busy")
        return {"busy_state": kwargs.get("busy_state")}

    async def release(target):
        calls["release"].append(target.id)
        return {}

    async def release_done(target):
        calls["release_done"].append(target.id)
        return {}

    async def status(target, **kwargs):
        return {}

    async def prepare(target, **kwargs):
        request_id = f"prep_{uuid.uuid4().hex[:8]}"
        calls["prepare"].append({"server_id": target.id, "prepare_request_id": request_id, **kwargs})
        return {"prepare_request_id": request_id, "status": "in_progress"}

    monkeypatch.setattr(server_client, "acquire_stand", acquire)
    monkeypatch.setattr(server_client, "release_stand", release)
    monkeypatch.setattr(server_client, "release_stand_as_done", release_done)
    monkeypatch.setattr(server_client, "set_stand_service_status", status)
    monkeypatch.setattr(server_client, "start_stand_prepare_for_test", prepare)
    return calls


async def _exit_code_test(client, token) -> str:
    test_id = await _create_test_def(client, token, None)
    resp = await client.patch(f"{TESTS_BASE}/{test_id}", headers=_hdr(token), json={"verdict_source": "exit_code"})
    assert resp.status_code == 200, resp.text
    return test_id


async def _scenario(client, token, stands: list[str], tests: list[str], *, second_prep="full",
                    second_extra: dict | None = None) -> dict:
    body = {
        "code": f"scn.{uuid.uuid4().hex[:8]}", "name": "Два стенда", "department_id": "dep_a",
        "readiness": "ready",
        "stands": [
            {"stand_id": stands[0], "label": "КД", "preparation": "full"},
            {"stand_id": stands[1], "label": "клиент", "preparation": second_prep, "kernel_override": "5.15.0-83",
             **(second_extra or {})},
        ],
        "actions": [
            {"kind": "run_test", "stand_id": stands[0], "test_id": tests[0]},
            {"kind": "run_test", "stand_id": stands[1], "test_id": tests[1], "is_verdict": True},
        ],
    }
    resp = await client.post(SCENARIOS, headers=_hdr(token), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _run(client, token, scenario_id) -> dict:
    resp = await client.post(
        f"{SCENARIOS}/{scenario_id}/runs", headers=_hdr(token),
        json={"os_version_id": LAUNCH_CTX["RC"], "kernel": LAUNCH_CTX["KERNEL"], "mode": "orel"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get(client, token, run_id) -> dict:
    resp = await client.get(f"{RUNS}/{run_id}", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _prepared(client, request_id: str, *, succeeded: bool = True) -> None:
    resp = await client.post(
        f"{CALLBACK_BASE}/{request_id}/completed", headers=_server_hdr("server_service", SERVER_SECRET),
        json={"correlation_id": "x", "succeeded": succeeded, **({} if succeeded else {"error": "restore failed"})},
    )
    assert resp.status_code == 200, resp.text


async def _claim(client) -> dict | None:
    resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
    assert resp.status_code == 200, resp.text
    return resp.json()["item"]


async def _complete(client, item_id, **body) -> None:
    resp = await client.post(
        f"{QUEUE_BASE}/{item_id}/completed", headers=_server_hdr("testing_worker", WORKER_SECRET),
        json={"succeeded": True, "exit_code": 0, **body},
    )
    assert resp.status_code == 200, resp.text


async def _stand_of(item_id: str) -> str:
    async with AsyncSessionLocal() as db:
        return (await db.get(QueueItem, item_id)).stand_id


class TestScenarioRun:
    async def test_two_stands_acquired_prepared_two_actions_then_released(
        self, client, admin_token, stands_api, configure_internal_keys, mock_git_token,
    ):
        """Приёмка: бронь обоих, подготовка, два действия последовательно, освобождение обоих."""
        await mock_git_token()
        (kd, kd_srv), (cl, cl_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [kd, cl], tests)

        run = await _run(client, admin_token, scn["id"])
        assert run["state"] == "preparing"
        assert sorted(stands_api["acquire"]) == sorted([kd_srv, cl_srv])
        prepares = {p["server_id"]: p for p in stands_api["prepare"]}
        assert set(prepares) == {kd_srv, cl_srv}
        assert prepares[cl_srv]["kernel"] == "5.15.0-83" and prepares[kd_srv]["kernel"] == LAUNCH_CTX["KERNEL"]

        # Пока готовится один стенд — действий нет.
        await _prepared(client, prepares[kd_srv]["prepare_request_id"])
        assert await _claim(client) is None
        await _prepared(client, prepares[cl_srv]["prepare_request_id"])
        run = await _get(client, admin_token, run["id"])
        assert run["state"] == "running" and run["current_position"] == 0

        first = await _claim(client)
        assert await _stand_of(first["queue_item_id"]) == kd
        # Второе действие ждёт конца первого.
        assert await _claim(client) is None
        await _complete(client, first["queue_item_id"])
        assert stands_api["release_done"] == []  # стенды всё ещё за сценарием

        second = await _claim(client)
        assert await _stand_of(second["queue_item_id"]) == cl
        await _complete(client, second["queue_item_id"])

        run = await _get(client, admin_token, run["id"])
        assert run["state"] == "succeeded" and run["verdict"] == "passed"
        assert [a["state"] for a in run["actions"]] == ["succeeded", "succeeded"]
        assert sorted(stands_api["release_done"]) == sorted([kd_srv, cl_srv])
        assert {s["state"] for s in run["stands"]} == {"released"}

    async def test_revert_only_stand_is_prepared_without_mode_switch_and_setup(
        self, client, admin_token, stands_api, configure_internal_keys,
    ):
        (kd, kd_srv), (cl, cl_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [kd, cl], tests, second_prep="revert_only")
        # Настройка на revert_only-стенде в сценарии задана, но уйти не должна.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import update

            from src.models import ScenarioStand

            await db.execute(
                update(ScenarioStand).where(ScenarioStand.stand_id == cl)
                .values(stand_setup={"kernel_cmdline_extra": ["audit=0"]})
            )
            await db.commit()

        await _run(client, admin_token, scn["id"])
        prepares = {p["server_id"]: p for p in stands_api["prepare"]}
        assert prepares[cl_srv]["preparation"] == "revert_only"
        assert prepares[cl_srv]["stand_setup"] is None
        assert prepares[cl_srv]["kernel"] == "5.15.0-83"
        # Полный стенд — без изменений.
        assert prepares[kd_srv]["preparation"] == "full"

    async def test_prepare_body_carries_revert_only_and_omits_full(self, monkeypatch):
        from src.services.stand_target import StandTarget

        sent: list[tuple[str, dict]] = []

        async def fake_post(path, body):
            sent.append((path, body))
            return {"prepare_request_id": "prep_1", "status": "in_progress"}

        monkeypatch.setattr(server_client, "_post_internal", fake_post)
        common = {
            "os_version_id": "osv_1", "kernel": "5.15.0-83", "mode": "orel", "test_username": "u",
            "requested_by_department_id": "dep_a",
        }
        await server_client.start_stand_prepare_for_test(
            StandTarget.vm("vm_1"), correlation_id="c1", preparation="revert_only", **common,
        )
        await server_client.start_stand_prepare_for_test(StandTarget.server("srv_1"), correlation_id="c2", **common)
        assert sent[0][0].endswith("/internal/vms/vm_1/prepare-for-test")
        assert sent[0][1]["preparation"] == "revert_only"
        assert sent[1][0].endswith("/internal/servers/srv_1/prepare-for-test")
        assert "preparation" not in sent[1][1]

    async def test_skip_pam_fix_passed_to_prepare(self, client, admin_token, stands_api, configure_internal_keys):
        (kd, kd_srv), (cl, cl_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(
            client, admin_token, [kd, cl], tests, second_prep="revert_only", second_extra={"skip_pam_fix": True},
        )

        run = await _run(client, admin_token, scn["id"])
        prepares = {p["server_id"]: p for p in stands_api["prepare"]}
        assert prepares[cl_srv]["skip_pam_fix"] is True
        assert prepares[cl_srv]["preparation"] == "revert_only"
        # Стенд без флага — как раньше.
        assert prepares[kd_srv]["skip_pam_fix"] is False
        assert {s["stand_id"]: s["skip_pam_fix"] for s in run["stands"]} == {kd: False, cl: True}

    async def test_skip_pam_fix_independent_of_preparation(
        self, client, admin_token, stands_api, configure_internal_keys,
    ):
        (kd, kd_srv), (cl, cl_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [kd, cl], tests, second_extra={"skip_pam_fix": True})

        await _run(client, admin_token, scn["id"])
        prepares = {p["server_id"]: p for p in stands_api["prepare"]}
        assert (prepares[cl_srv]["preparation"], prepares[cl_srv]["skip_pam_fix"]) == ("full", True)
        assert (prepares[kd_srv]["preparation"], prepares[kd_srv]["skip_pam_fix"]) == ("full", False)

    async def test_prepare_body_carries_skip_pam_fix_only_when_set(self, monkeypatch):
        from src.services.stand_target import StandTarget

        sent: list[dict] = []

        async def fake_post(path, body):
            sent.append(body)
            return {"prepare_request_id": "prep_1", "status": "in_progress"}

        monkeypatch.setattr(server_client, "_post_internal", fake_post)
        common = {
            "os_version_id": "osv_1", "kernel": "5.15.0-83", "mode": "orel", "test_username": "u",
            "requested_by_department_id": "dep_a",
        }
        await server_client.start_stand_prepare_for_test(
            StandTarget.vm("vm_1"), correlation_id="c1", skip_pam_fix=True, **common,
        )
        await server_client.start_stand_prepare_for_test(
            StandTarget.server("srv_1"), correlation_id="c2", skip_pam_fix=True, **common,
        )
        await server_client.start_stand_prepare_for_test(StandTarget.server("srv_2"), correlation_id="c3", **common)
        assert sent[0]["skip_pam_fix"] is True and sent[1]["skip_pam_fix"] is True
        assert "skip_pam_fix" not in sent[2]

    async def test_busy_stand_run_waits_and_releases_taken_reservation(
        self, client, admin_token, stands_api, configure_internal_keys,
    ):
        """Приёмка: один стенд занят — сценарий ждёт, уже взятая бронь отпущена."""
        (a, a_srv), (b, b_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [a, b], tests)
        first_srv, second_srv = sorted([(a, a_srv), (b, b_srv)])[0][1], sorted([(a, a_srv), (b, b_srv)])[1][1]
        stands_api["busy"].add(second_srv)

        run = await _run(client, admin_token, scn["id"])
        assert run["state"] == "waiting_for_stands"
        assert run["blocked_by"][0]["reason"] == "SERVER_ALREADY_BUSY"
        assert stands_api["acquire"] == [first_srv, second_srv]  # порядок — по stand_id
        assert stands_api["release"] == [first_srv]
        assert stands_api["prepare"] == []
        async with AsyncSessionLocal() as db:
            assert not await scenario_queue.holds_stand(db, a)

        # Стенд освободился — тик берёт оба.
        stands_api["busy"].clear()
        async with AsyncSessionLocal() as db:
            await scenario_queue.tick(db)
        run = await _get(client, admin_token, run["id"])
        assert run["state"] == "preparing" and run["blocked_by"] == []

    async def test_active_stand_queue_blocks_scenario(self, client, admin_token, stands_api, configure_internal_keys):
        (a, _), (b, _) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        single = await _create_test_def(client, admin_token, b)
        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), single, launch_context=LAUNCH_CTX)
        scn = await _scenario(client, admin_token, [a, b], tests)
        run = await _run(client, admin_token, scn["id"])
        assert run["state"] == "waiting_for_stands"
        assert run["blocked_by"] == [{"stand_id": b, "reason": "stand_queue_active"}]
        assert len(stands_api["acquire"]) == 1  # только одиночный item

    async def test_single_test_enqueued_during_scenario_runs_after_it(
        self, client, admin_token, stands_api, configure_internal_keys, mock_git_token,
    ):
        """Приёмка: одностендовый тест, поставленный во время сценария, идёт после него."""
        await mock_git_token()
        (kd, kd_srv), (cl, cl_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [kd, cl], tests, second_prep="none")
        run = await _run(client, admin_token, scn["id"])
        kd_prep = stands_api["prepare"][0]["prepare_request_id"]

        single = await _create_test_def(client, admin_token, kd)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), single, launch_context=LAUNCH_CTX)
        assert item.state == QueueItemState.QUEUED
        assert len(stands_api["prepare"]) == 1  # одиночный цикл не стартовал

        await _prepared(client, kd_prep)
        first = await _claim(client)
        await _complete(client, first["queue_item_id"])
        second = await _claim(client)
        await _complete(client, second["queue_item_id"])
        assert (await _get(client, admin_token, run["id"]))["state"] == "succeeded"

        # Сценарий кончился: бронь КД перешла одиночному item'у (без освобождения),
        # клиент — в testing_done.
        async with AsyncSessionLocal() as db:
            single_item = await db.get(QueueItem, item.id)
        assert single_item.state == QueueItemState.PREPARING
        assert stands_api["prepare"][-1]["correlation_id"] == item.id
        assert stands_api["release_done"] == [cl_srv]

    async def test_failed_non_verdict_action_fails_scenario_and_releases(
        self, client, admin_token, stands_api, configure_internal_keys, mock_git_token,
    ):
        await mock_git_token()
        (kd, kd_srv), (cl, cl_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [kd, cl], tests, second_prep="none")
        run = await _run(client, admin_token, scn["id"])
        await _prepared(client, stands_api["prepare"][0]["prepare_request_id"])
        first = await _claim(client)
        await _complete(client, first["queue_item_id"], succeeded=False, exit_code=2)

        run = await _get(client, admin_token, run["id"])
        assert run["state"] == "failed" and "action 1" in run["error"]
        assert run["actions"][1]["state"] is None  # второе действие не запускалось
        assert await _claim(client) is None
        async with AsyncSessionLocal() as db:
            retries = [i for i in (await db.execute(
                QueueItem.__table__.select().where(QueueItem.retry_of_id == first["queue_item_id"])
            )).all()]
        assert retries == []  # действие сценария не ретраится
        assert sorted(stands_api["release_done"]) == sorted([kd_srv, cl_srv])

    async def test_prepare_failure_fails_scenario(self, client, admin_token, stands_api, configure_internal_keys):
        (kd, kd_srv), (cl, cl_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [kd, cl], tests)
        run = await _run(client, admin_token, scn["id"])
        by_srv = {p["server_id"]: p["prepare_request_id"] for p in stands_api["prepare"]}
        await _prepared(client, by_srv[kd_srv], succeeded=False)
        run = await _get(client, admin_token, run["id"])
        assert run["state"] == "failed"
        # КД отпущен сразу, клиент — когда закончит готовиться.
        assert stands_api["release_done"] == [kd_srv]
        await _prepared(client, by_srv[cl_srv])
        assert sorted(stands_api["release_done"]) == sorted([kd_srv, cl_srv])
        assert await _claim(client) is None

    async def test_stop_skips_current_action_and_releases(
        self, client, admin_token, stands_api, configure_internal_keys, mock_git_token,
    ):
        await mock_git_token()
        (kd, kd_srv), (cl, cl_srv) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [kd, cl], tests, second_prep="none")
        run = await _run(client, admin_token, scn["id"])
        await _prepared(client, stands_api["prepare"][0]["prepare_request_id"])
        first = await _claim(client)

        resp = await client.post(f"{RUNS}/{run['id']}/stop", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "stopping"
        # Воркер обрывает сессию по заявке.
        await _complete(client, first["queue_item_id"], succeeded=False, interrupted="skip")
        run = await _get(client, admin_token, run["id"])
        assert run["state"] == "stopped"
        assert run["actions"][0]["state"] == "skipped" and run["actions"][1]["state"] is None
        assert sorted(stands_api["release_done"]) == sorted([kd_srv, cl_srv])

    async def test_wait_action_is_advanced_by_tick(self, client, admin_token, stands_api, configure_internal_keys):
        (kd, _), (cl, _) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        test_id = await _exit_code_test(client, admin_token)
        resp = await client.post(SCENARIOS, headers=_hdr(admin_token), json={
            "code": f"scn.{uuid.uuid4().hex[:8]}", "name": "wait", "department_id": "dep_a", "readiness": "ready",
            "stands": [{"stand_id": kd, "preparation": "none"}, {"stand_id": cl, "preparation": "none"}],
            "actions": [
                {"kind": "wait", "params": {"seconds": 30}},
                {"kind": "run_test", "stand_id": cl, "test_id": test_id, "is_verdict": True},
            ],
        })
        assert resp.status_code == 201, resp.text
        run = await _run(client, admin_token, resp.json()["id"])
        assert run["state"] == "running" and run["wait_until"] is not None
        async with AsyncSessionLocal() as db:
            await scenario_queue.tick(db)
        assert (await _get(client, admin_token, run["id"]))["current_position"] == 0
        async with AsyncSessionLocal() as db:
            row = await db.get(ScenarioRun, run["id"])
            row.wait_until = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
            await scenario_queue.tick(db)
        run = await _get(client, admin_token, run["id"])
        assert run["current_position"] == 1 and run["actions"][1]["state"] == "ready"

    async def test_not_ready_scenario_requires_debug_and_other_department_denied(
        self, client, admin_token, make_token, stands_api,
    ):
        (kd, _), (cl, _) = await _create_stand(client, admin_token), await _create_stand(client, admin_token)
        tests = [await _exit_code_test(client, admin_token), await _exit_code_test(client, admin_token)]
        scn = await _scenario(client, admin_token, [kd, cl], tests)
        body = {**scn, "readiness": "development"}
        body["stands"] = [{k: v for k, v in s.items() if k not in ("id", "target_type", "stand_name")}
                          for s in scn["stands"]]
        body["actions"] = [{k: v for k, v in a.items() if k not in ("id", "position", "test_code")}
                           for a in scn["actions"]]
        assert (await client.put(f"{SCENARIOS}/{scn['id']}", headers=_hdr(admin_token), json=body)).status_code == 200
        resp = await client.post(
            f"{SCENARIOS}/{scn['id']}/runs", headers=_hdr(admin_token),
            json={"os_version_id": LAUNCH_CTX["RC"], "kernel": LAUNCH_CTX["KERNEL"]},
        )
        assert resp.status_code == 422 and resp.json()["error_code"] == "SCENARIO_REQUIRES_DEBUG"
        other = make_token(department_id="dep_b", service_roles={"testing_service": ["admin"]})
        resp = await client.post(
            f"{SCENARIOS}/{scn['id']}/runs", headers=_hdr(other),
            json={"os_version_id": LAUNCH_CTX["RC"], "kernel": LAUNCH_CTX["KERNEL"], "debug": True},
        )
        assert resp.status_code == 403
