"""Шаг настройки стенда теста и профили подготовки в prepare-for-test."""

from __future__ import annotations

import pytest

from src.services import queue as queue_svc
from src.services import server_client
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    LAUNCH_CTX,
    TESTS_BASE,
    _create_stand,
    _create_test_def,
    _get_item,
    _identity,
    configure_internal_keys,  # noqa: F401 — фикстура
    mock_server_service,  # noqa: F401 — фикстура
    recorded_calls,  # noqa: F401 — зависимость mock_server_service
)
from src.db.session import AsyncSessionLocal

PP_BASE = "/api/testing/v1/provisioning-profiles"
LEGACY_PROVISIONING = {
    "allowed_failed_units": ["astra-mount-lock.service"],
    "degraded_reboot_attempts": 3,
    "disable_pam_lastlog_inactive": True,
    "boot_wait_timeout_seconds": None,
}


@pytest.fixture
def captured_prepare(monkeypatch):
    calls: list[dict] = []

    async def fake_start(server_id, **kwargs):
        calls.append(kwargs)
        return {"prepare_request_id": f"prep_{len(calls)}", "status": "in_progress"}

    monkeypatch.setattr(server_client, "start_prepare_for_test", fake_start)
    return calls


@pytest.fixture
def dept_admin_token(make_token, dept_a) -> str:
    return make_token(department_id=dept_a, platform_role="department_admin")


async def _test_with_setup(client, admin_token, stand_id, setup: dict | None, **extra) -> str:
    test_id = await _create_test_def(client, admin_token, stand_id)
    body = {"department_id": "dep_a", **extra}
    if setup is not None:
        body["stand_setup"] = setup
    resp = await client.patch(f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token), json=body)
    assert resp.status_code == 200, resp.text
    return test_id


class TestPrepareForTestBody:
    async def test_default_provisioning_and_no_setup(
        self, client, admin_token, mock_server_service, configure_internal_keys, captured_prepare,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _test_with_setup(client, admin_token, stand_id, None)
        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert captured_prepare[0]["provisioning"] == LEGACY_PROVISIONING
        assert captured_prepare[0]["stand_setup"] is None

    async def test_setup_script_is_resolved_and_sensitive_flagged(
        self, client, admin_token, mock_server_service, configure_internal_keys, captured_prepare,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _test_with_setup(client, admin_token, stand_id, {
            "kernel_cmdline_extra": ["audit=0"],
            "script": "echo {{TEST_USER}} ${HOME} {{TEST_PASSWORD}}\n",
            "run_as": "test_user",
        })
        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        setup = captured_prepare[0]["stand_setup"]
        assert setup["kernel_cmdline_extra"] == ["audit=0"]
        # {{CODE}} — подстановка, ${HOME} — bash, не трогается.
        assert setup["script"] == "echo u ${HOME} default-test-password\n"
        assert setup["script_is_sensitive"] is True
        assert setup["run_as"] == "test_user"
        assert setup["phase"] == "after_boot" and setup["reboot_after"] is True

    async def test_unknown_variable_fails_before_reservation(
        self, client, admin_token, mock_server_service, configure_internal_keys, captured_prepare,
        recorded_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _test_with_setup(client, admin_token, stand_id, {"script": "echo {{NO_SUCH_VAR}}\n"})
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        stored = await _get_item(item.id)
        assert stored.state == "failed"
        assert stored.failed_step == "stand_setup"
        assert "NO_SUCH_VAR" in stored.error
        assert captured_prepare == []
        assert not any(p.endswith("/acquire-for-service") for _, p in recorded_calls)

    async def test_unsafe_kernel_param_rejected_on_save(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        resp = await client.patch(
            f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token),
            json={"stand_setup": {"kernel_cmdline_extra": ["audit=0 quiet"]}},
        )
        assert resp.status_code == 422


class TestProvisioningProfiles:
    async def test_department_profile_overrides_global(
        self, client, admin_token, dept_admin_token, mock_server_service, configure_internal_keys,
        captured_prepare, dept_a,
    ):
        listed = (await client.get(PP_BASE, headers=_hdr(admin_token), params={"department_id": dept_a})).json()
        assert [p["name"] for p in listed["items"]] == ["Легаси"]
        assert listed["items"][0]["department_id"] is None

        resp = await client.post(PP_BASE, headers=_hdr(dept_admin_token), json={
            "department_id": dept_a, "name": "Без PAM", "is_default": True,
            "allowed_failed_units": ["astra-mount-lock.service", "foo.service"],
            "degraded_reboot_attempts": 1, "disable_pam_lastlog_inactive": False,
        })
        assert resp.status_code == 201, resp.text

        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _test_with_setup(client, admin_token, stand_id, None)
        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert captured_prepare[0]["provisioning"] == {
            "allowed_failed_units": ["astra-mount-lock.service", "foo.service"],
            "degraded_reboot_attempts": 1, "disable_pam_lastlog_inactive": False,
            "boot_wait_timeout_seconds": None,
        }

    async def test_department_admin_cannot_edit_global(self, client, dept_admin_token):
        resp = await client.patch(f"{PP_BASE}/pp_default", headers=_hdr(dept_admin_token), json={"degraded_reboot_attempts": 0})
        assert resp.status_code == 403

    async def test_foreign_profile_rejected_on_test(self, client, admin_token, make_token, mock_server_service):
        mock_server_service()
        other = make_token(department_id="dep_other", platform_role="department_admin")
        foreign = (await client.post(PP_BASE, headers=_hdr(other), json={"department_id": "dep_other", "name": "x"})).json()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        resp = await client.patch(
            f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token),
            json={"department_id": "dep_a", "provisioning_profile_id": foreign["id"]},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "PROVISIONING_PROFILE_INVALID"


class TestStandSetupCallback:
    async def test_server_service_callback_accepted(self, client, configure_internal_keys):
        from tests.test_queue import SERVER_SECRET, _server_hdr

        resp = await client.post(
            "/internal/stand-setup/ssr_1/completed", headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": "qi_1:step2", "succeeded": False, "failed_step": "stand_setup", "error": "boom"},
        )
        assert resp.status_code == 200, resp.text

    async def test_other_identity_rejected(self, client, configure_internal_keys):
        from tests.test_queue import WORKER_SECRET, _server_hdr

        resp = await client.post(
            "/internal/stand-setup/ssr_1/completed", headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"correlation_id": "qi_1", "succeeded": True},
        )
        assert resp.status_code in (401, 403)
