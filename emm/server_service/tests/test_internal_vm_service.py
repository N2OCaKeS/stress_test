"""ВМ-стенды для testing_service.

Зеркало `test_internal_service_reservation.py` / `test_prepare_for_test.py`
для `/internal/vms/{vm_id}/…`:

* бронь от имени сервиса: захват (CAS), 409 на занятую/человеческую/
  lifecycle-занятую ВМ, takeover человеческой брони, release/смена стадии
  только своей брони, `testing_done` снимает человек;
* сервисная бронь гейтит человеческие операции (даже админа);
* connection-info, список снимков с разбором имени по шаблонам,
  смешанный batch-status, настройка шаблонов;
* prepare-for-test: выбор снимка с нормализацией версии, `VM_SNAPSHOT_NOT_FOUND`
  → `failed_step=vm_revert` без похода на hub, диспатч `vm.prepare_for_test`,
  callback воркера → callback в testing_service, снятие своей брони на провале.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.models import Vm, VmSnapshot
from src.models.server_prepare_for_test import (
    PREPARE_FOR_TEST_FAILED,
    PREPARE_FOR_TEST_IN_PROGRESS,
    PREPARE_FOR_TEST_SUCCEEDED,
    STEP_VM_REVERT,
    ServerPrepareForTestRequest,
)
from src.utils.ids import prepare_for_test_request_id, vm_snapshot_id
from tests._helpers import auth_hdr as _user_hdr
from tests.test_prepare_for_test import (
    _make_os_version,
    _svc_hdr,
    captured_dispatch as captured_dispatch,
    configure_service_keys as configure_service_keys,
    fake_acs as fake_acs,
)
from tests.test_prepare_for_test_test_account import (
    CRED_ID,
    captured_callbacks as captured_callbacks,
    captured_stash as captured_stash,
    fake_secret as fake_secret,
)
from tests.test_vm_domain import make_hub as make_hub, make_vm as make_vm

BASE = "/api/server/v1/internal/vms"
SERVERS = "/api/server/v1/internal/servers"
ACS_SECRET = "test-acs-secret-do-not-use-in-prod"


@pytest.fixture
def both_service_keys(monkeypatch):
    from src.core.config import get_settings as _get_settings

    monkeypatch.setenv(
        "SERVER_INBOUND_SERVICE_API_KEYS",
        "testing_service=test-testing-service-secret-do-not-use-in-prod,"
        f"acs={ACS_SECRET}",
    )
    _get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    _get_settings.cache_clear()  # type: ignore[attr-defined]


async def _row(db, vm_id: str) -> Vm:
    stmt = select(Vm).where(Vm.id == vm_id).execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one()


async def _snapshot(db, vm, name: str, *, is_system: bool = False, state: str = "ready", **extra) -> VmSnapshot:
    snap = VmSnapshot(
        id=vm_snapshot_id(), vm_id=vm.id, name=name, is_system=is_system, state=state,
        kind=extra.pop("kind", "os_baseline"), **extra,
    )
    db.add(snap)
    await db.flush()
    return snap


class TestVmAcquire:
    async def test_acquire_free_vm(self, client, db, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        resp = await client.post(
            f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(),
            json={"busy_state": "acs", "busy_note": "ACS|revert|1.8|6.1", "requested_by_department_id": "dep_a"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["vm_id"] == vm.id
        assert body["busy_state"] == "acs"
        assert body["busy_actor_type"] == "service"
        assert body["busy_service_name"] == "testing_service"
        row = await _row(db, vm.id)
        assert row.service_busy_state == "acs"
        assert row.status == "run test"
        # lifecycle-lock не трогается — это другая сущность.
        assert row.busy_state is None

    async def test_second_acquire_conflicts(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        first = await client.post(f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={})
        assert first.status_code == 200, first.text
        second = await client.post(f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={})
        assert second.status_code == 409, second.text
        assert second.json()["error_code"] == "VM_ALREADY_BUSY"
        assert second.json()["details"]["current_state"] == "acs"

    async def test_human_booking_conflicts_without_takeover(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub(), status="alice")
        resp = await client.post(f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={})
        assert resp.status_code == 409
        assert resp.json()["details"]["current_state"] == "busy"

    async def test_takeover_human_booking(self, client, db, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub(), status="alice")
        resp = await client.post(
            f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={"takeover": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["previous_holder"]["busy_state"] == "busy"
        assert resp.json()["previous_holder"]["busy_note"] == "alice"
        assert (await _row(db, vm.id)).status == "run test"

    async def test_lifecycle_operation_not_taken_over(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub(), busy_state="reverting")
        resp = await client.post(
            f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={"takeover": True},
        )
        assert resp.status_code == 409
        assert resp.json()["details"]["current_state"] == "updating"

    async def test_department_mismatch_masked_as_404(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        resp = await client.post(
            f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(),
            json={"requested_by_department_id": "dep_b"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "VM_NOT_FOUND"

    async def test_unknown_identity_rejected(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        resp = await client.post(
            f"{BASE}/{vm.id}/acquire-for-service",
            headers=_svc_hdr(identity="rogue_service"), json={},
        )
        assert resp.status_code == 403


class TestVmReleaseAndStatus:
    async def test_release_own(self, client, db, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        await client.post(f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={})
        resp = await client.post(f"{BASE}/{vm.id}/release-for-service", headers=_svc_hdr())
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "free"
        row = await _row(db, vm.id)
        assert (row.status, row.service_busy_state, row.busy_service_name) == ("free", None, None)

    async def test_other_service_cannot_release(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        await client.post(f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={})
        resp = await client.post(
            f"{BASE}/{vm.id}/release-for-service", headers=_svc_hdr(ACS_SECRET, identity="acs"),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "VM_RESERVED_BY_OTHER"

    async def test_release_not_busy(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        resp = await client.post(f"{BASE}/{vm.id}/release-for-service", headers=_svc_hdr())
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "VM_NOT_BUSY"

    async def test_status_change_keeps_since(self, client, db, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        await client.post(f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={})
        since = (await _row(db, vm.id)).service_busy_since
        resp = await client.post(
            f"{BASE}/{vm.id}/service-status", headers=_svc_hdr(),
            json={"busy_state": "testing", "busy_note": "t|1.8|6.1"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "testing"
        row = await _row(db, vm.id)
        assert row.service_busy_since == since
        assert row.busy_note == "t|1.8|6.1"

    async def test_release_as_done_then_human_acknowledges(
        self, client, db, make_hub, make_vm, both_service_keys, admin_role_token_a,
    ):
        vm = await make_vm(hub=await make_hub())
        await client.post(f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={})
        resp = await client.post(f"{BASE}/{vm.id}/release-for-service-as-done", headers=_svc_hdr())
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "testing_done"
        human = await client.post(
            f"/api/server/v1/vms/{vm.id}/release", headers=_user_hdr(admin_role_token_a),
        )
        assert human.status_code == 200, human.text
        row = await _row(db, vm.id)
        assert (row.status, row.service_busy_state) == ("free", None)


class TestHumanGate:
    async def test_admin_cannot_touch_vm_under_service_reservation(
        self, client, make_hub, make_vm, both_service_keys, admin_role_token_a,
    ):
        vm = await make_vm(hub=await make_hub())
        await client.post(f"{BASE}/{vm.id}/acquire-for-service", headers=_svc_hdr(), json={})
        for method, path, body in (
            ("post", f"/api/server/v1/vms/{vm.id}/reserve", {}),
            ("post", f"/api/server/v1/vms/{vm.id}/release", None),
            ("post", f"/api/server/v1/vms/{vm.id}/power", {"action": "reboot"}),
        ):
            kwargs = {"headers": _user_hdr(admin_role_token_a)}
            if body is not None:
                kwargs["json"] = body
            resp = await getattr(client, method)(path, **kwargs)
            assert resp.status_code == 409, (path, resp.text)
            assert resp.json()["error_code"] == "VM_RESERVED_BY_SERVICE", path


class TestConnectionInfoAndBatch:
    async def test_connection_info(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.8.7")
        resp = await client.get(f"{BASE}/{vm.id}/connection-info", headers=_svc_hdr())
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"vm_id": vm.id, "host": "10.9.8.7"}

    async def test_connection_info_without_ip(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        resp = await client.get(f"{BASE}/{vm.id}/connection-info", headers=_svc_hdr())
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "VM_NO_IP_ADDRESS"

    async def test_mixed_batch_status(self, client, make_server, make_hub, make_vm, both_service_keys):
        srv = await make_server()
        free_vm = await make_vm(hub=await make_hub())
        booked_vm = await make_vm(hub=await make_hub(), status="bob")
        resp = await client.post(
            f"{SERVERS}/batch-status", headers=_svc_hdr(),
            json={"server_ids": [srv.id], "vm_ids": [free_vm.id, booked_vm.id, "vm_missing"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [s["server_id"] for s in body["servers"]] == [srv.id]
        vms = {v["vm_id"]: v for v in body["vms"]}
        assert vms[free_vm.id]["busy_state"] == "free"
        assert vms[booked_vm.id]["busy_state"] == "busy"
        assert vms[booked_vm.id]["busy_note"] == "bob"
        assert vms["vm_missing"]["found"] is False

    async def test_vm_only_batch_status(self, client, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        resp = await client.post(f"{SERVERS}/batch-status", headers=_svc_hdr(), json={"vm_ids": [vm.id]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["servers"] == []


class TestSnapshotsAndTemplates:
    async def test_list_parses_names_by_templates(self, client, db, make_hub, make_vm, both_service_keys):
        vm = await make_vm(hub=await make_hub())
        await _snapshot(db, vm, "1710rc52")
        await _snapshot(db, vm, "1.8.1.6_smolensk")
        await _snapshot(db, vm, "1.8.1.6_build", is_system=True)
        await _snapshot(db, vm, "broken", state="error")
        await _snapshot(db, vm, "my-work", kind="user")
        resp = await client.get(f"{BASE}/{vm.id}/snapshots", headers=_svc_hdr())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["templates"] == ["{version}", "{version}_{mode}"]
        items = {i["name"]: i for i in body["snapshots"]}
        assert set(items) == {"1710rc52", "1.8.1.6_smolensk", "my-work"}
        assert items["1710rc52"]["normalized_version"] == "1.7.10.52"
        assert items["1710rc52"]["mode"] is None
        assert items["1.8.1.6_smolensk"]["version_name"] == "1.8.1.6"
        assert items["1.8.1.6_smolensk"]["mode"] == "smolensk"
        assert items["my-work"]["version_name"] is None

    async def test_settings_roundtrip_and_validation(self, client, account_admin_token, admin_role_token_a):
        url = "/api/server/v1/settings/vm-test"
        resp = await client.get(url, headers=_user_hdr(account_admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["snapshot_name_templates"] == ["{version}", "{version}_{mode}"]
        bad = await client.put(
            url, headers=_user_hdr(account_admin_token), json={"snapshot_name_templates": ["{hostname}"]},
        )
        assert bad.status_code == 422
        assert bad.json()["error_code"] == "VM_SNAPSHOT_TEMPLATE_INVALID"
        ok = await client.put(
            url, headers=_user_hdr(account_admin_token),
            json={"snapshot_name_templates": ["{hostname}-{version}", "{version}"]},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["snapshot_name_templates"] == ["{hostname}-{version}", "{version}"]
        denied = await client.get(url, headers=_user_hdr(admin_role_token_a))
        assert denied.status_code == 403


def _pft_body(os_version_id: str, **overrides) -> dict:
    body = {
        "os_version_id": os_version_id,
        "kernel": "6.1.90",
        "mode": "orel",
        "test_username": "u",
        "correlation_id": "qi_vm_1",
        "requested_by_department_id": "dep_a",
        "test_account_credential_id": CRED_ID,
        "target": {"type": "vm"},
    }
    body.update(overrides)
    return body


async def _request(db, request_id: str) -> ServerPrepareForTestRequest:
    return (await db.execute(select(ServerPrepareForTestRequest).where(
        ServerPrepareForTestRequest.id == request_id,
    ).execution_options(populate_existing=True))).scalar_one()


class TestVmPrepareForTest:
    async def test_compact_snapshot_name_selected_and_dispatched(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch,
        fake_secret, captured_stash,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.0.5")
        await _snapshot(db, vm, "1710rc52")
        await _snapshot(db, vm, "1.7.11.17")
        osv = await _make_os_version(db, name="1.7.10.52", kernels=["6.1.90"])

        resp = await client.post(
            f"{BASE}/{vm.id}/prepare-for-test", headers=_svc_hdr(), json=_pft_body(osv.id),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "in_progress"
        assert [c["task_kind"] for c in captured_dispatch] == ["vm.prepare_for_test"]
        payload = captured_dispatch[0]["payload"]
        assert payload["snapshot_name"] == "1710rc52"
        assert payload["guest_ip"] == "10.9.0.5"
        assert payload["vm_id"] == vm.id
        stash = captured_stash[payload["test_creds_key"]]
        assert stash["test_username"] == "u"
        assert stash["test_ssh_public_key"].startswith("ssh-ed25519")
        # Не-managed ВМ без кред снимка — базовая учётка образа.
        assert stash["guest_login_user"] and stash["guest_password"]
        assert "test_password" not in payload and "guest_password" not in payload

        row = await _request(db, resp.json()["prepare_request_id"])
        assert (row.server_id, row.vm_id, row.vm_snapshot_name) == (None, vm.id, "1710rc52")
        assert row.reservation_acquired is True
        vm_row = await _row(db, vm.id)
        assert (vm_row.service_busy_state, vm_row.status) == ("acs", "run test")

        status = await client.get(
            f"{BASE}/{vm.id}/prepare-for-test/{row.id}", headers=_svc_hdr(),
        )
        assert status.status_code == 200, status.text
        assert status.json()["vm_id"] == vm.id
        assert status.json()["server_id"] is None

    async def test_mode_template_picks_mode_snapshot(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch,
        fake_secret, captured_stash,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.0.6")
        await _snapshot(db, vm, "1.8.1.6_orel")
        await _snapshot(db, vm, "1.8.1.6_smolensk")
        osv = await _make_os_version(db, name="1.8.1.6", kernels=["6.1.90"])
        resp = await client.post(
            f"{BASE}/{vm.id}/prepare-for-test", headers=_svc_hdr(),
            json=_pft_body(osv.id, mode="smolensk", correlation_id="qi_vm_mode"),
        )
        assert resp.status_code == 202, resp.text
        assert captured_dispatch[0]["payload"]["snapshot_name"] == "1.8.1.6_smolensk"

    async def test_snapshot_not_found_fails_vm_revert_without_dispatch(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch,
        fake_secret, captured_callbacks,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.0.7")
        await _snapshot(db, vm, "1.7.11.17")
        osv = await _make_os_version(db, name="1.7.10.52", kernels=["6.1.90"])
        resp = await client.post(
            f"{BASE}/{vm.id}/prepare-for-test", headers=_svc_hdr(),
            json=_pft_body(osv.id, correlation_id="qi_vm_nf"),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "failed"
        assert captured_dispatch == []
        row = await _request(db, resp.json()["prepare_request_id"])
        assert row.failed_step == STEP_VM_REVERT
        assert "VM_SNAPSHOT_NOT_FOUND" in row.error and "1.7.10.52" in row.error
        assert captured_callbacks[-1]["failed_step"] == "vm_revert"
        # Бронь запрос не брал — ВМ свободна.
        assert (await _row(db, vm.id)).service_busy_state is None

    async def test_vm_without_test_account_fails_user_provision(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch,
        captured_callbacks,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.0.8")
        osv = await _make_os_version(db, name="1.7.10.53", kernels=["6.1.90"])
        body = _pft_body(osv.id, correlation_id="qi_vm_ta")
        body.pop("test_account_credential_id")
        resp = await client.post(f"{BASE}/{vm.id}/prepare-for-test", headers=_svc_hdr(), json=body)
        assert resp.status_code == 202
        row = await _request(db, resp.json()["prepare_request_id"])
        assert row.failed_step == "user_provision"
        assert "TEST_ACCOUNT_NOT_CONFIGURED" in row.error

    async def test_target_mismatch_on_server_path(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.post(
            f"{SERVERS}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_pft_body("osv_x", target={"type": "vm", "vm_id": "vm_x"}),
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "PREPARE_TARGET_MISMATCH"

    async def test_busy_vm_rejected(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch, fake_secret,
    ):
        vm = await make_vm(hub=await make_hub(), ip_address="10.9.0.9", status="alice")
        await _snapshot(db, vm, "1.7.10.54")
        osv = await _make_os_version(db, name="1.7.10.54", kernels=["6.1.90"])
        resp = await client.post(
            f"{BASE}/{vm.id}/prepare-for-test", headers=_svc_hdr(),
            json=_pft_body(osv.id, correlation_id="qi_vm_busy"),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "VM_ALREADY_BUSY"


class TestVmPrepareCallback:
    async def _row(self, db, vm, *, reservation_acquired: bool) -> ServerPrepareForTestRequest:
        req = ServerPrepareForTestRequest(
            id=prepare_for_test_request_id(), server_id=None, vm_id=vm.id,
            correlation_id=f"qi_cb_{vm.id}", os_version_id="osv_cb", kernel="6.1.90", mode="orel",
            test_username="u", test_account_credential_id=CRED_ID,
            requested_by_service="testing_service", status=PREPARE_FOR_TEST_IN_PROGRESS,
            stage=STEP_VM_REVERT, reservation_acquired=reservation_acquired,
        )
        db.add(req)
        await db.flush()
        return req

    async def test_success_forwards_callback_without_creds(
        self, client, db, make_hub, make_vm, worker_bot_token_a, captured_callbacks,
    ):
        vm = await make_vm(hub=await make_hub())
        req = await self._row(db, vm, reservation_acquired=False)
        resp = await client.post(
            f"{BASE}/{vm.id}/prepare-for-test-done",
            headers={**_user_hdr(worker_bot_token_a), "X-Target-Department-Id": "dep_a"},
            json={"prepare_request_id": req.id, "succeeded": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == PREPARE_FOR_TEST_SUCCEEDED
        assert captured_callbacks[-1] == {"correlation_id": req.correlation_id, "succeeded": True}

    async def test_failure_releases_reservation_taken_by_request(
        self, client, db, make_hub, make_vm, worker_bot_token_a, captured_callbacks,
    ):
        vm = await make_vm(hub=await make_hub(), status="run test")
        vm.service_busy_state = "acs"
        vm.busy_service_name = "testing_service"
        await db.flush()
        req = await self._row(db, vm, reservation_acquired=True)
        resp = await client.post(
            f"{BASE}/{vm.id}/prepare-for-test-done",
            headers={**_user_hdr(worker_bot_token_a), "X-Target-Department-Id": "dep_a"},
            json={"prepare_request_id": req.id, "succeeded": False, "failed_step": "vm_revert",
                  "error": "VM_SNAPSHOT_FAILED: revert failed"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == PREPARE_FOR_TEST_FAILED
        assert captured_callbacks[-1]["failed_step"] == "vm_revert"
        row = await _row(db, vm.id)
        assert (row.service_busy_state, row.status) == (None, "free")

    async def test_foreign_request_404(self, client, db, make_hub, make_vm, worker_bot_token_a):
        vm = await make_vm(hub=await make_hub())
        other = await make_vm(hub=await make_hub())
        req = await self._row(db, other, reservation_acquired=False)
        resp = await client.post(
            f"{BASE}/{vm.id}/prepare-for-test-done",
            headers={**_user_hdr(worker_bot_token_a), "X-Target-Department-Id": "dep_a"},
            json={"prepare_request_id": req.id, "succeeded": True},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "PREPARE_REQUEST_NOT_FOUND"
