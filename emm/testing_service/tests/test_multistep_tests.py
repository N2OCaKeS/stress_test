"""Многоступенчатые тесты — шаги теста (API) и исполнение шагов очередью.

Моки server_service (`mock_server_service` + перехват `start_prepare_for_test`
и `start_stand_setup`; у ВМ-стенда — HTTP-мок `mock_vm_service`) и воркера
(internal-эндпоинты claim/completed).
"""

from __future__ import annotations

import uuid

import pytest

from src.core.constants import QueueItemState
from src.db.session import AsyncSessionLocal
from src.repositories import test_log as test_log_repo
from src.repositories import test_log_segment as test_log_segment_repo
from src.services import queue as queue_svc
from src.services import server_client, zephyr_verdict
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
    _get_item,
    _identity,
    _server_hdr,
    claim_file,
    configure_internal_keys,  # noqa: F401 — фикстура
    mock_git_token,  # noqa: F401 — фикстура
    mock_server_service,  # noqa: F401 — фикстура
    recorded_calls,  # noqa: F401 — зависимость mock_server_service
)
from tests.test_vm_stands import _create_vm_stand, mock_vm_service  # noqa: F401 — фикстура

STAND_SETUP_BASE = "/internal/stand-setup"


def _steps_url(test_id: str) -> str:
    return f"{TESTS_BASE}/{test_id}/steps"


async def _steps(client, token, test_id) -> list[dict]:
    resp = await client.get(_steps_url(test_id), headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _add_step(client, token, test_id, **body) -> dict:
    resp = await client.post(_steps_url(test_id), headers=_hdr(token), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _slots(client, token, test_id, step_id=None) -> list[dict]:
    params = {"step_id": step_id} if step_id else None
    resp = await client.get(f"{TESTS_BASE}/{test_id}/args", headers=_hdr(token), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── API шагов ────────────────────────────────────────────────────────────────

class TestStepsApi:
    async def test_new_test_has_one_full_step_with_its_slots(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id, starter_suffix="kernel")
        steps = await _steps(client, admin_token, test_id)
        assert len(steps) == 1
        assert (steps[0]["position"], steps[0]["run_mode"], steps[0]["starter_suffix"]) == (0, "full", "kernel")
        slots = await _slots(client, admin_token, test_id)
        assert [s["literal_value"] for s in slots] == ["--run"]
        assert slots[0]["step_id"] == steps[0]["id"]

    async def test_test_fields_are_first_step_values(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        setup = {"kernel_cmdline_extra": ["audit=0"]}
        resp = await client.patch(
            f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token),
            json={"stand_setup": setup, "starter_suffix": "oom"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stand_setup"]["kernel_cmdline_extra"] == ["audit=0"]
        assert resp.json()["starter_suffix"] == "oom"
        step = (await _steps(client, admin_token, test_id))[0]
        assert step["stand_setup"]["kernel_cmdline_extra"] == ["audit=0"]
        assert step["starter_suffix"] == "oom"
        listed = await client.get(TESTS_BASE, headers=_hdr(admin_token), params={"limit": 500})
        mine = next(t for t in listed.json()["items"] if t["id"] == test_id)
        assert mine["starter_suffix"] == "oom"

    async def test_add_copy_reorder_delete(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        first = (await _steps(client, admin_token, test_id))[0]

        second = await _add_step(
            client, admin_token, test_id, name="maxcpus=16", copy_args_from_step_id=first["id"],
            stand_setup={"kernel_cmdline_extra": ["maxcpus=16"]},
        )
        assert (second["position"], second["run_mode"]) == (1, "rerun")
        copied = await _slots(client, admin_token, test_id, second["id"])
        assert [s["literal_value"] for s in copied] == ["--run"]

        # Слоты шагов независимы.
        resp = await client.post(
            f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
            json={"step_id": second["id"], "kind": "literal", "literal_value": "-q"},
        )
        assert resp.status_code == 201, resp.text
        assert [s["literal_value"] for s in await _slots(client, admin_token, test_id)] == ["--run"]
        assert [s["literal_value"] for s in await _slots(client, admin_token, test_id, second["id"])] == ["--run", "-q"]

        # rerun первым — нельзя.
        resp = await client.put(
            f"{_steps_url(test_id)}/order", headers=_hdr(admin_token),
            json={"step_ids": [second["id"], first["id"]]},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "TEST_STEP_FIRST_MUST_BE_FULL"

        third = await _add_step(client, admin_token, test_id, name="full again", run_mode="full")
        resp = await client.put(
            f"{_steps_url(test_id)}/order", headers=_hdr(admin_token),
            json={"step_ids": [first["id"], third["id"], second["id"]]},
        )
        assert resp.status_code == 200, resp.text
        assert [s["name"] for s in await _steps(client, admin_token, test_id)] == ["", "full again", "maxcpus=16"]

        resp = await client.put(
            f"{_steps_url(test_id)}/order", headers=_hdr(admin_token), json={"step_ids": [first["id"]]},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "TEST_STEP_ORDER_STALE"

        resp = await client.delete(f"{_steps_url(test_id)}/{third['id']}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert [s["position"] for s in await _steps(client, admin_token, test_id)] == [0, 1]
        resp = await client.delete(f"{_steps_url(test_id)}/{second['id']}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        resp = await client.delete(f"{_steps_url(test_id)}/{first['id']}", headers=_hdr(admin_token))
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "TEST_STEP_LAST"

    async def test_foreign_step_is_404(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_a = await _create_test_def(client, admin_token, stand_id)
        test_b = await _create_test_def(client, admin_token, stand_id)
        step_b = (await _steps(client, admin_token, test_b))[0]
        resp = await client.get(f"{TESTS_BASE}/{test_a}/args", headers=_hdr(admin_token), params={"step_id": step_b["id"]})
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TEST_STEP_NOT_FOUND"
        resp = await client.patch(f"{_steps_url(test_a)}/{step_b['id']}", headers=_hdr(admin_token), json={"name": "x"})
        assert resp.status_code == 404

    async def test_write_requires_update_permission(self, client, admin_token, no_role_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        resp = await client.post(_steps_url(test_id), headers=_hdr(no_role_token), json={"name": "x"})
        assert resp.status_code == 403


# ── очередь ──────────────────────────────────────────────────────────────────

@pytest.fixture
def captured_server(monkeypatch):
    """Перехват prepare-for-test и stand-setup (C2): тела запросов."""
    calls: dict[str, list[dict]] = {"prepare": [], "stand_setup": []}

    async def fake_prepare(server_id, **kwargs):
        calls["prepare"].append(kwargs)
        return {"prepare_request_id": f"prep_{uuid.uuid4().hex[:8]}", "status": "in_progress"}

    async def fake_stand_setup(server_id, **kwargs):
        calls["stand_setup"].append({"server_id": server_id, **kwargs})
        return {"stand_setup_request_id": f"ssr_{len(calls['stand_setup'])}", "status": "in_progress"}

    monkeypatch.setattr(server_client, "start_prepare_for_test", fake_prepare)
    monkeypatch.setattr(server_client, "start_stand_setup", fake_stand_setup)
    return calls


@pytest.fixture
def zephyr_target(monkeypatch):
    """У item'а есть прогон в Zephyr — после успеха последнего шага ждём вердикт."""
    calls: list[str] = []

    async def fake_resolve_target(db, item, department_id):
        calls.append(item.id)
        return zephyr_verdict.ZephyrTarget(
            base_url="https://jira", credential_id="cred", test_run_key="BT-C1",
            test_case_key="BT-T1", test_case_title="Тест очереди",
        )

    monkeypatch.setattr(zephyr_verdict, "resolve_target", fake_resolve_target)
    return calls


async def _two_step_test(client, token, stand_id, *, second_setup: dict | None) -> tuple[str, dict, dict]:
    test_id = await _create_test_def(client, token, stand_id, starter_suffix="kernel")
    # Общий легаси-профиль явно: другие тесты оставляют профиль отдела `dep_a`
    # по умолчанию со своей командой и без скрипта повторного запуска.
    resp = await client.patch(f"{TESTS_BASE}/{test_id}", headers=_hdr(token), json={"launch_profile_id": "lp_default"})
    assert resp.status_code == 200, resp.text
    first = (await _steps(client, token, test_id))[0]
    resp = await client.patch(
        f"{_steps_url(test_id)}/{first['id']}", headers=_hdr(token), json={"name": "phase one"},
    )
    assert resp.status_code == 200, resp.text
    body = {"name": "phase two", "copy_args_from_step_id": first["id"]}
    if second_setup is not None:
        body["stand_setup"] = second_setup
    second = await _add_step(client, token, test_id, **body)
    resp = await client.post(
        f"{TESTS_BASE}/{test_id}/args", headers=_hdr(token),
        json={"step_id": second["id"], "kind": "literal", "literal_value": "-sf"},
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        f"{TESTS_BASE}/{test_id}/args", headers=_hdr(token),
        json={"step_id": second["id"], "kind": "literal", "literal_value": "end"},
    )
    assert resp.status_code == 201, resp.text
    return test_id, first, second


async def _prepared(client, item) -> None:
    resp = await client.post(
        f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
        headers=_server_hdr("server_service", SERVER_SECRET),
        json={"correlation_id": item.id, "succeeded": True},
    )
    assert resp.status_code == 200, resp.text


async def _claim(client) -> dict:
    resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
    assert resp.status_code == 200, resp.text
    payload = resp.json()["item"]
    assert payload is not None
    return payload


async def _complete(client, item_id, **body) -> None:
    resp = await client.post(
        f"{QUEUE_BASE}/{item_id}/completed", headers=_server_hdr("testing_worker", WORKER_SECRET),
        json={"succeeded": True, "exit_code": 0, **body},
    )
    assert resp.status_code == 200, resp.text


async def _stand_setup_done(client, correlation_id, **body) -> None:
    resp = await client.post(
        f"{STAND_SETUP_BASE}/ssr_x/completed", headers=_server_hdr("server_service", SERVER_SECRET),
        json={"correlation_id": correlation_id, "succeeded": True, **body},
    )
    assert resp.status_code == 200, resp.text


async def _log_labels(item_id: str) -> list[str]:
    async with AsyncSessionLocal() as db:
        log = await test_log_repo.get_by_queue_item_id(db, item_id)
        if log is None:
            return []
        return [s.label for s in await test_log_segment_repo.list_by_log(db, log.id)]


class TestQueueSteps:
    async def test_two_steps_with_stand_setup_between_and_verdict_after_last(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
        captured_server, zephyr_target,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, server_id = await _create_stand(client, admin_token)
        test_id, first, second = await _two_step_test(client, admin_token, stand_id, second_setup={
            "kernel_cmdline_extra": ["maxcpus=16"], "script": "echo {{STEP_NAME}} {{TEST_USER}}\n",
            "reboot_after": False,
        })

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        # Первый шаг: его настройки нет — prepare-for-test без stand_setup.
        assert captured_server["prepare"][0]["stand_setup"] is None
        await _prepared(client, item)

        payload = await _claim(client)
        assert payload["step"] == {"index": 0, "count": 2, "name": "phase one"}
        assert claim_file(payload, "dates_")["content"] == "--run"
        assert claim_file(payload, "starter.sh")["content"].startswith("#!/bin/bash")
        assert any(f["path"].rsplit("/", 1)[1].startswith("git_token_") for f in payload["files"])

        await _complete(client, item.id)
        stored = await _get_item(item.id)
        # Шаг 1 не последний: вердикт не выносится, стенд уходит в настройку.
        assert stored.state == QueueItemState.PREPARING
        assert (stored.current_step_index, stored.step_count) == (1, 2)
        assert stored.verdict is None and zephyr_target == []
        [setup_call] = captured_server["stand_setup"]
        assert setup_call["server_id"] == server_id
        assert setup_call["correlation_id"] == stored.stand_setup_correlation_id
        assert setup_call["correlation_id"].startswith(f"{item.id}:step1:")
        assert setup_call["stand_setup"]["kernel_cmdline_extra"] == ["maxcpus=16"]
        assert setup_call["stand_setup"]["script"] == "echo phase two u\n"
        assert setup_call["stand_setup"]["reboot_after"] is False
        assert setup_call["test_username"] == "u"
        assert setup_call["provisioning"]["allowed_failed_units"] == ["astra-mount-lock.service"]

        # Публичный item показывает прогресс по шагам.
        resp = await client.get(
            "/api/testing/v1/queue-items", headers=_hdr(admin_token),
            params={"kind": "all", "stand_id": stand_id},
        )
        assert resp.status_code == 200, resp.text
        listed = next(i for i in resp.json()["items"] if i["id"] == item.id)
        assert (listed["current_step_index"], listed["step_count"]) == (1, 2)

        await _stand_setup_done(client, setup_call["correlation_id"])
        stored = await _get_item(item.id)
        assert stored.state == QueueItemState.READY

        payload = await _claim(client)
        assert payload["step"] == {"index": 1, "count": 2, "name": "phase two"}
        assert claim_file(payload, "dates_")["content"] == "--run -sf end"
        # rerun: скрипт повторного запуска по пути starter.sh, без клонирования и токена.
        script = claim_file(payload, "starter.sh")["content"]
        assert "run.py" in script and "clone" not in script
        assert not any(f["path"].rsplit("/", 1)[1].startswith("git_token_") for f in payload["files"])
        assert payload["launch_command"].split()[-1] == "kernel"

        await _complete(client, item.id)
        stored = await _get_item(item.id)
        # Вердикт — только после последнего шага.
        assert stored.state == QueueItemState.AWAITING_VERDICT
        assert zephyr_target == [item.id]

        labels = await _log_labels(item.id)
        assert "Настройка стенда перед: шаг 2/2 «phase two»" in labels

    async def test_vm_stand_setup_between_steps_goes_through_vm_channel(
        self, client, admin_token, mock_vm_service, configure_internal_keys, mock_git_token,
        recorded_calls, captured_server,
    ):
        handle = mock_vm_service(host="10.7.7.7")
        await mock_git_token()
        stand_id, vm_id = await _create_vm_stand(client, admin_token)
        test_id, _first, _second = await _two_step_test(client, admin_token, stand_id, second_setup={
            "kernel_cmdline_extra": ["maxcpus=16"], "script": "echo {{STEP_NAME}}\n",
        })

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert handle.state["prepare_bodies"][0]["target"] == {"type": "vm", "vm_id": vm_id}
        await _prepared(client, item)
        payload = await _claim(client)
        assert payload["step"]["index"] == 0

        recorded_calls.clear()
        await _complete(client, item.id)
        stored = await _get_item(item.id)
        assert stored.state == QueueItemState.PREPARING
        assert stored.error is None
        # Настройка без отката — по пути ВМ, серверный канал не трогается.
        [(path, body)] = handle.state["stand_setup_calls"]
        assert path == f"/api/server/v1/internal/vms/{vm_id}/stand-setup"
        assert captured_server["stand_setup"] == []
        assert body["correlation_id"] == stored.stand_setup_correlation_id
        assert body["correlation_id"].startswith(f"{item.id}:step1:")
        assert body["stand_setup"]["kernel_cmdline_extra"] == ["maxcpus=16"]
        assert body["stand_setup"]["script"] == "echo phase two\n"
        assert body["test_username"] == "u"
        assert not any("/internal/servers/" in p and not p.endswith("/batch-status") for _, p in recorded_calls)

        await _stand_setup_done(client, body["correlation_id"])
        assert (await _get_item(item.id)).state == QueueItemState.READY
        payload = await _claim(client)
        assert payload["step"] == {"index": 1, "count": 2, "name": "phase two"}
        assert payload["host"] == "10.7.7.7"
        labels = await _log_labels(item.id)
        assert "Настройка стенда перед: шаг 2/2 «phase two»" in labels

    async def test_step_without_stand_setup_goes_straight_to_ready(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, captured_server,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id, _first, _second = await _two_step_test(client, admin_token, stand_id, second_setup=None)
        resp = await client.patch(f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token), json={"verdict_source": "exit_code"})
        assert resp.status_code == 200, resp.text

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await _prepared(client, item)
        await _claim(client)
        await _complete(client, item.id)
        stored = await _get_item(item.id)
        assert stored.state == QueueItemState.READY
        assert captured_server["stand_setup"] == []
        payload = await _claim(client)
        assert payload["step"]["index"] == 1
        await _complete(client, item.id)
        stored = await _get_item(item.id)
        assert (stored.state, stored.verdict) == (QueueItemState.SUCCEEDED, "passed")

    async def test_failed_step_fails_item_with_step_in_error(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, captured_server,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id, _first, _second = await _two_step_test(client, admin_token, stand_id, second_setup=None)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await _prepared(client, item)
        await _claim(client)
        await _complete(client, item.id)
        await _claim(client)
        await _complete(client, item.id, succeeded=False, exit_code=3)
        stored = await _get_item(item.id)
        assert stored.state == QueueItemState.FAILED
        assert stored.error.startswith("Шаг 2/2 «phase two»: ")
        assert "exit_code=3" in stored.error

    async def test_failed_stand_setup_fails_item(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, captured_server,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id, _first, _second = await _two_step_test(
            client, admin_token, stand_id, second_setup={"kernel_cmdline_extra": ["maxcpus=16"]},
        )
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await _prepared(client, item)
        await _claim(client)
        await _complete(client, item.id)
        correlation_id = captured_server["stand_setup"][0]["correlation_id"]
        await _stand_setup_done(client, correlation_id, succeeded=False, failed_step="reboot_verify", error="no boot")
        stored = await _get_item(item.id)
        assert stored.state == QueueItemState.FAILED
        assert stored.failed_step == "reboot_verify"
        assert "no boot" in stored.error and "шаг 2/2" in stored.error
        # Повтор callback'а — no-op.
        await _stand_setup_done(client, correlation_id)
        assert (await _get_item(item.id)).state == QueueItemState.FAILED

    async def test_unknown_stand_setup_callback_is_noop(self, client, configure_internal_keys):
        await _stand_setup_done(client, "qi_missing:step1:deadbeef")

    async def test_prepare_only_runs_first_step_only(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, captured_server,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id, _first, _second = await _two_step_test(client, admin_token, stand_id, second_setup=None)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX, prepare_only=True)
        await _prepared(client, item)
        payload = await _claim(client)
        assert payload["step"]["index"] == 0
        await _complete(client, item.id)
        assert (await _get_item(item.id)).state == QueueItemState.PREPARED


class TestLaunchPreviewSteps:
    async def test_preview_builds_the_job_of_the_chosen_step(
        self, client, admin_token, guest_token, mock_server_service, mock_git_token,
    ):
        """Превью запуска по шагу: dates и настройка стенда шага, `rerun` — без токена."""
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id, _first, second = await _two_step_test(client, admin_token, stand_id, second_setup={
            "kernel_cmdline_extra": ["maxcpus=16"], "script": "echo {{STEP_NAME}}\n",
        })
        resp = await client.patch(
            f"{_steps_url(test_id)}/{second['id']}", headers=_hdr(admin_token), json={"run_mode": "rerun"},
        )
        assert resp.status_code == 200, resp.text
        body = {"stand_id": stand_id, "os_version_id": LAUNCH_CTX["RC"], "kernel": LAUNCH_CTX["KERNEL"]}

        first = (await client.post(
            f"{TESTS_BASE}/{test_id}/launch-preview", headers=_hdr(guest_token), json=body,
        )).json()
        assert first["step"] == {"index": 0, "count": 2, "name": "phase one", "run_mode": "full"}
        assert first["dates_content_masked"] == "--run"
        assert "token" in {f["role"] for f in first["files"]}
        assert first.get("stand_setup") is None

        resp = await client.post(
            f"{TESTS_BASE}/{test_id}/launch-preview", headers=_hdr(guest_token), json={**body, "step_index": 1},
        )
        assert resp.status_code == 200, resp.text
        second_preview = resp.json()
        assert second_preview["errors"] == []
        assert second_preview["step"] == {"index": 1, "count": 2, "name": "phase two", "run_mode": "rerun"}
        assert second_preview["dates_content_masked"] == "--run -sf end"
        assert "token" not in {f["role"] for f in second_preview["files"]}
        assert second_preview["stand_setup"]["kernel_cmdline_extra"] == ["maxcpus=16"]
        assert second_preview["stand_setup"]["script"] == "echo phase two\n"
        rows = {row["code"]: row for row in second_preview["variables"]}
        assert rows["STEP_INDEX"]["value"] == "2"
