"""`skip_pam_fix` в prepare-for-test.

Флаг стенда сценария: воркеру уходит `skip_pam_fix`, шаг `pam_fix` не
выполняется, профиль подготовки при этом передаётся как есть. От
`preparation` не зависит. Без флага — прежнее поведение.
"""

from __future__ import annotations

from src.services import prepare_for_test as pft_svc
from tests.test_internal_vm_service import BASE as VMS, _pft_body, _snapshot
from tests.test_prepare_for_test import (
    BASE,
    _body,
    _make_os_version,
    _set_bootstrap_password,
    _svc_hdr,
    captured_dispatch as captured_dispatch,
    configure_service_keys as configure_service_keys,
    fake_acs as fake_acs,
)
from tests.test_prepare_for_test_revert_only import _row
from tests.test_prepare_for_test_test_account import fake_secret as fake_secret
from tests.test_stand_setup_provisioning import PROVISIONING, SETUP, captured_stash as captured_stash
from tests.test_vm_domain import make_hub as make_hub, make_vm as make_vm


class TestServerSkipPamFix:
    async def _start(self, client, db, make_server, correlation_id: str, **extra):
        srv = await make_server()
        osv = await _make_os_version(db, name=f"1.8-{correlation_id}", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, correlation_id=correlation_id, stand_setup=SETUP,
                       provisioning=PROVISIONING, **extra),
        )
        assert resp.status_code == 202, resp.text
        return srv, await _row(db, resp.json()["prepare_request_id"])

    async def test_flag_persisted_and_dispatched(
        self, client, db, make_server, configure_service_keys, captured_dispatch, captured_stash,
    ):
        srv, row = await self._start(client, db, make_server, "qi_nopam_srv", skip_pam_fix=True)
        assert row.skip_pam_fix is True
        assert row.preparation == "full"

        await pft_svc.on_server_prepared(db, srv)
        payload = captured_dispatch[-1]["payload"]
        assert payload["skip_pam_fix"] is True
        assert "skip_mode_switch" not in payload
        assert payload["provisioning"]["disable_pam_lastlog_inactive"] is True
        assert payload["stand_setup"]["kernel_cmdline_extra"] == ["audit=0"]

        status = await client.get(f"{BASE}/{srv.id}/prepare-for-test/{row.id}", headers=_svc_hdr())
        assert status.json()["skip_pam_fix"] is True

    async def test_default_unchanged(
        self, client, db, make_server, configure_service_keys, captured_dispatch, captured_stash,
    ):
        srv, row = await self._start(client, db, make_server, "qi_pam_srv")
        assert row.skip_pam_fix is False

        await pft_svc.on_server_prepared(db, srv)
        payload = captured_dispatch[-1]["payload"]
        assert "skip_pam_fix" not in payload
        assert payload["provisioning"]["disable_pam_lastlog_inactive"] is True

        status = await client.get(f"{BASE}/{srv.id}/prepare-for-test/{row.id}", headers=_svc_hdr())
        assert status.json()["skip_pam_fix"] is False

    async def test_independent_of_revert_only(
        self, client, db, make_server, configure_service_keys, captured_dispatch, captured_stash,
    ):
        srv, row = await self._start(
            client, db, make_server, "qi_ro_nopam_srv", preparation="revert_only", skip_pam_fix=True,
        )
        assert (row.preparation, row.skip_pam_fix) == ("revert_only", True)

        await pft_svc.on_server_prepared(db, srv)
        payload = captured_dispatch[-1]["payload"]
        assert payload["skip_mode_switch"] is True
        assert payload["skip_pam_fix"] is True

    async def test_revert_only_alone_keeps_pam(
        self, client, db, make_server, configure_service_keys, captured_dispatch, captured_stash,
    ):
        srv, _row_ = await self._start(client, db, make_server, "qi_ro_pam_srv", preparation="revert_only")
        await pft_svc.on_server_prepared(db, srv)
        assert "skip_pam_fix" not in captured_dispatch[-1]["payload"]

    async def test_non_bool_rejected(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_body(correlation_id="qi_bad_pam", skip_pam_fix="sometimes"),
        )
        assert resp.status_code == 422


class TestVmSkipPamFix:
    async def test_flag_in_vm_payload(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch,
        fake_secret, captured_stash,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.3.5")
        await _snapshot(db, vm, "1.7.5.9")
        osv = await _make_os_version(db, name="1.7.5.9", kernels=["6.1.90"])
        resp = await client.post(
            f"{VMS}/{vm.id}/prepare-for-test", headers=_svc_hdr(),
            json=_pft_body(osv.id, correlation_id="qi_nopam_vm", skip_pam_fix=True, provisioning=PROVISIONING),
        )
        assert resp.status_code == 202, resp.text
        [task] = captured_dispatch
        assert task["task_kind"] == "vm.prepare_for_test"
        payload = task["payload"]
        assert payload["skip_pam_fix"] is True
        assert "skip_mode_switch" not in payload
        assert payload["provisioning"]["disable_pam_lastlog_inactive"] is True
        row = await _row(db, resp.json()["prepare_request_id"])
        assert row.skip_pam_fix is True

        status = await client.get(f"{VMS}/{vm.id}/prepare-for-test/{row.id}", headers=_svc_hdr())
        assert status.status_code == 200, status.text
        assert status.json()["skip_pam_fix"] is True

    async def test_default_vm_payload_unchanged(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch,
        fake_secret, captured_stash,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.3.6")
        await _snapshot(db, vm, "1.7.5.10")
        osv = await _make_os_version(db, name="1.7.5.10", kernels=["6.1.90"])
        resp = await client.post(
            f"{VMS}/{vm.id}/prepare-for-test", headers=_svc_hdr(),
            json=_pft_body(osv.id, correlation_id="qi_pam_vm"),
        )
        assert resp.status_code == 202, resp.text
        assert "skip_pam_fix" not in captured_dispatch[0]["payload"]
        row = await _row(db, resp.json()["prepare_request_id"])
        assert row.skip_pam_fix is False
