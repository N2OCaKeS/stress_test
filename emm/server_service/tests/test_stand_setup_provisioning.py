"""`stand_setup` и `provisioning` в prepare-for-test, операция «настройка без restore»."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.models import ServerStandSetupRequest
from src.models.server_prepare_for_test import ServerPrepareForTestRequest
from src.services import prepare_for_test as pft_svc
from src.services import stand_setup as ss_svc
from tests._helpers import auth_hdr as _user_hdr
from tests.test_prepare_for_test import (
    BASE,
    _body,
    _make_os_version,
    _set_bootstrap_password,
    _svc_hdr,
    captured_dispatch,  # noqa: F401 — фикстура
    configure_service_keys,  # noqa: F401 — фикстура
    fake_acs,  # noqa: F401 — зависимость captured_dispatch
)

SETUP = {
    "kernel_cmdline_extra": ["audit=0"],
    "script": "echo tune 's3cr3t'\n",
    "script_is_sensitive": True,
    "run_as": "root",
    "phase": "after_boot",
    "reboot_after": True,
    "timeout_seconds": 600,
}
PROVISIONING = {
    "allowed_failed_units": ["astra-mount-lock.service"],
    "degraded_reboot_attempts": 3,
    "disable_pam_lastlog_inactive": True,
}


@pytest.fixture
def captured_stash(monkeypatch):
    stored: dict[str, dict] = {}

    async def fake_store(key, value):
        stored[key] = value

    async def fake_delete(key):
        stored.pop(key, None)

    import src.services.worker_client as worker_mod

    monkeypatch.setattr(worker_mod, "store_dispatch_creds", fake_store)
    monkeypatch.setattr(worker_mod, "delete_dispatch_creds", fake_delete)
    return stored


class TestPrepareForTestCarriesSetup:
    async def test_script_encrypted_and_provisioning_reaches_acs_and_worker(
        self, client, make_server, db, configure_service_keys, captured_dispatch, captured_stash,
    ):
        srv = await make_server()
        osv = await _make_os_version(db, name="1.8-ss", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, correlation_id="qi_ss_1", stand_setup=SETUP, provisioning=PROVISIONING),
        )
        assert resp.status_code == 202, resp.text
        row = (await db.execute(select(ServerPrepareForTestRequest).where(
            ServerPrepareForTestRequest.id == resp.json()["prepare_request_id"],
        ))).scalar_one()
        # Текст скрипта не лежит открытым ни в JSONB, ни в payload задач.
        assert "script" not in row.stand_setup
        assert row.stand_setup["kernel_cmdline_extra"] == ["audit=0"]
        assert row.stand_setup_script_encrypted and "s3cr3t" not in row.stand_setup_script_encrypted
        assert pft_svc.read_stand_setup_script(row) == SETUP["script"]
        acs = captured_dispatch[0]
        assert acs["task_kind"] == "acs.snapshot_restore"
        assert acs["payload"]["provisioning"]["allowed_failed_units"] == ["astra-mount-lock.service"]

        await pft_svc.on_server_prepared(db, srv)
        worker = captured_dispatch[-1]
        assert worker["task_kind"] == "server.prepare_for_test"
        assert worker["payload"]["stand_setup"]["kernel_cmdline_extra"] == ["audit=0"]
        assert worker["payload"]["provisioning"]["degraded_reboot_attempts"] == 3
        assert "s3cr3t" not in str(worker["payload"])
        stash = captured_stash[worker["payload"]["test_creds_key"]]
        assert stash["stand_setup_script"] == SETUP["script"]

    async def test_unsafe_kernel_param_rejected(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_body(correlation_id="qi_ss_bad", stand_setup={"kernel_cmdline_extra": ["audit=0; reboot"]}),
        )
        assert resp.status_code == 422


class TestStandSetupWithoutRestore:
    async def test_start_dispatches_task_with_script_in_stash(
        self, client, make_server, db, configure_service_keys, captured_dispatch, captured_stash,
    ):
        srv = await make_server()
        resp = await client.post(
            f"{BASE}/{srv.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_ss_2:step1", "stand_setup": SETUP, "provisioning": PROVISIONING},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "in_progress" and body["stand_setup_request_id"].startswith("ssr_")
        task = captured_dispatch[-1]
        assert task["task_kind"] == "server.stand_setup"
        assert "s3cr3t" not in str(task["payload"])
        assert captured_stash[task["payload"]["script_key"]] == {"stand_setup_script": SETUP["script"]}

        again = await client.post(
            f"{BASE}/{srv.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_ss_2:step1", "stand_setup": SETUP},
        )
        assert again.json()["stand_setup_request_id"] == body["stand_setup_request_id"]
        assert len([c for c in captured_dispatch if c["task_kind"] == "server.stand_setup"]) == 1

    @pytest.mark.usefixtures("soft_dept_mode")
    async def test_worker_failure_is_forwarded_to_testing_service(
        self, client, make_server, db, dept_a, worker_bot_token_a,
        configure_service_keys, captured_dispatch, captured_stash, monkeypatch,
    ):
        srv = await make_server(department_id=dept_a)
        start = await ss_svc.start(
            db, server_id=srv.id, service_name="testing_service", correlation_id="qi_ss_3",
            requested_by_department_id=dept_a, test_username="u", stand_setup=SETUP, provisioning=None,
        )
        sent: list[tuple[str, dict]] = []

        async def fake_send(request_id, body):
            sent.append((request_id, body))
            return True, 1, None

        monkeypatch.setattr("src.services.testing_client.send_stand_setup_completed", fake_send)
        resp = await client.post(
            f"{BASE}/{srv.id}/stand-setup-done", headers=_user_hdr(worker_bot_token_a),
            json={"stand_setup_request_id": start.id, "succeeded": False,
                  "failed_step": "stand_setup", "error": "script exited with 3"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "failed"
        assert sent == [(start.id, {
            "correlation_id": "qi_ss_3", "succeeded": False,
            "failed_step": "stand_setup", "error": "script exited with 3",
        })]
        row = (await db.execute(select(ServerStandSetupRequest).where(
            ServerStandSetupRequest.id == start.id,
        ))).scalar_one()
        assert row.status == "failed" and row.callback_delivered_at is not None
