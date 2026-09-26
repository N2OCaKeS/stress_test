"""`preparation` в prepare-for-test: `revert_only` против `full`.

`revert_only` — restore/откат, учётка и ядро; без смены режима (воркеру —
`skip_mode_switch`) и без шага настройки стенда. `full` (и отсутствие поля)
— прежнее поведение.
"""

from __future__ import annotations

from sqlalchemy import select

from src.models.server_prepare_for_test import ServerPrepareForTestRequest
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
from tests.test_prepare_for_test_test_account import fake_secret as fake_secret
from tests.test_stand_setup_provisioning import PROVISIONING, SETUP, captured_stash as captured_stash
from tests.test_vm_domain import make_hub as make_hub, make_vm as make_vm


async def _row(db, request_id: str) -> ServerPrepareForTestRequest:
    return (await db.execute(select(ServerPrepareForTestRequest).where(
        ServerPrepareForTestRequest.id == request_id,
    ).execution_options(populate_existing=True))).scalar_one()


class TestServerPreparation:
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

    async def test_revert_only_skips_mode_switch_and_stand_setup(
        self, client, db, make_server, configure_service_keys, captured_dispatch, captured_stash,
    ):
        srv, row = await self._start(client, db, make_server, "qi_ro_srv", preparation="revert_only")
        assert row.preparation == "revert_only"
        assert row.stand_setup is None and row.stand_setup_script_encrypted is None
        # restore — как обычно.
        assert captured_dispatch[0]["task_kind"] == "acs.snapshot_restore"

        await pft_svc.on_server_prepared(db, srv)
        worker = captured_dispatch[-1]
        assert worker["task_kind"] == "server.prepare_for_test"
        payload = worker["payload"]
        assert payload["skip_mode_switch"] is True
        assert payload["kernel"] == "5.10.0"
        assert "stand_setup" not in payload
        assert "stand_setup_script" not in captured_stash[payload["test_creds_key"]]
        # PAM-правка — по профилю подготовки, не по объёму подготовки.
        assert payload["provisioning"]["disable_pam_lastlog_inactive"] is True

        status = await client.get(f"{BASE}/{srv.id}/prepare-for-test/{row.id}", headers=_svc_hdr())
        assert status.json()["preparation"] == "revert_only"

    async def test_full_by_default_is_unchanged(
        self, client, db, make_server, configure_service_keys, captured_dispatch, captured_stash,
    ):
        srv, row = await self._start(client, db, make_server, "qi_full_srv")
        assert row.preparation == "full"
        assert row.stand_setup["kernel_cmdline_extra"] == ["audit=0"]

        await pft_svc.on_server_prepared(db, srv)
        payload = captured_dispatch[-1]["payload"]
        assert "skip_mode_switch" not in payload
        assert payload["mode"] == "orel"
        assert payload["stand_setup"]["kernel_cmdline_extra"] == ["audit=0"]
        assert captured_stash[payload["test_creds_key"]]["stand_setup_script"] == SETUP["script"]

    async def test_unknown_preparation_rejected(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_body(correlation_id="qi_bad_prep", preparation="none"),
        )
        assert resp.status_code == 422


class TestVmPreparation:
    async def test_revert_only_vm_payload(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch,
        fake_secret, captured_stash,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.2.5")
        await _snapshot(db, vm, "1.7.5.9")
        osv = await _make_os_version(db, name="1.7.5.9", kernels=["6.1.90"])
        resp = await client.post(
            f"{VMS}/{vm.id}/prepare-for-test", headers=_svc_hdr(),
            json=_pft_body(osv.id, correlation_id="qi_ro_vm", preparation="revert_only", stand_setup=SETUP),
        )
        assert resp.status_code == 202, resp.text
        [task] = captured_dispatch
        assert task["task_kind"] == "vm.prepare_for_test"
        payload = task["payload"]
        assert payload["skip_mode_switch"] is True
        assert payload["snapshot_name"] == "1.7.5.9"
        assert payload["kernel"] == "6.1.90"
        assert "stand_setup" not in payload
        assert "stand_setup_script" not in captured_stash[payload["test_creds_key"]]
        row = await _row(db, resp.json()["prepare_request_id"])
        assert (row.preparation, row.stand_setup) == ("revert_only", None)

    async def test_full_vm_payload_unchanged(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch,
        fake_secret, captured_stash,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.2.6")
        await _snapshot(db, vm, "1.7.5.10")
        osv = await _make_os_version(db, name="1.7.5.10", kernels=["6.1.90"])
        resp = await client.post(
            f"{VMS}/{vm.id}/prepare-for-test", headers=_svc_hdr(),
            json=_pft_body(osv.id, correlation_id="qi_full_vm", stand_setup=SETUP),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert "skip_mode_switch" not in payload
        assert payload["stand_setup"]["kernel_cmdline_extra"] == ["audit=0"]
