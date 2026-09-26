"""Настройка ВМ-стенда без отката — `POST /internal/vms/{id}/stand-setup`.

* диспатч `vm.stand_setup`: учётка входа в гостя и скрипт — только в stash,
  вход — учёткой снимка последней успешной подготовки;
* ВМ не под бронью вызывающего — 409;
* повтор по `correlation_id` — тот же запрос, второй задачи нет;
* callback воркера → тот же callback в testing_service, что у сервера.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.models import ServerStandSetupRequest, VmSnapshot
from src.models.server_prepare_for_test import PREPARE_FOR_TEST_SUCCEEDED, ServerPrepareForTestRequest
from src.services import secrets_service
from src.utils.ids import prepare_for_test_request_id, vm_snapshot_id
from tests._helpers import auth_hdr as _user_hdr
from tests.test_prepare_for_test import (
    _svc_hdr,
    captured_dispatch as captured_dispatch,
    configure_service_keys as configure_service_keys,
    fake_acs as fake_acs,
)
from tests.test_stand_setup_provisioning import PROVISIONING, SETUP, captured_stash as captured_stash
from tests.test_vm_domain import make_hub as make_hub, make_vm as make_vm

BASE = "/api/server/v1/internal/vms"


async def _held_vm(db, make_hub, make_vm, *, holder: str | None = "testing_service", ip: str = "10.9.1.5"):
    vm = await make_vm(hub=await make_hub(), ip_address=ip, status="run test" if holder else "free")
    if holder:
        vm.service_busy_state = "testing"
        vm.busy_service_name = holder
    await db.flush()
    return vm


async def _prepared_on_snapshot(db, vm, name: str) -> VmSnapshot:
    """Снимок с парольной учёткой + успешная подготовка, откатившая на него."""
    snap = VmSnapshot(
        id=vm_snapshot_id(), vm_id=vm.id, name=name, is_system=False, state="ready", kind="os_baseline",
        mgmt_user="snapuser",
    )
    snap.mgmt_password_encrypted = secrets_service.encrypt(
        "snap-pass", aad=secrets_service.aad_for_vm_snapshot_password(snap.id),
    )
    db.add(snap)
    db.add(ServerPrepareForTestRequest(
        id=prepare_for_test_request_id(), server_id=None, vm_id=vm.id, vm_snapshot_name=name,
        correlation_id=f"qi_prev_{vm.id}", os_version_id="osv_x", kernel="6.1.90", mode="orel",
        test_username="u", requested_by_service="testing_service", status=PREPARE_FOR_TEST_SUCCEEDED,
        stage="reboot_verify", completed_at=datetime.now(timezone.utc),
    ))
    await db.flush()
    return snap


@pytest.fixture
def sent_callbacks(monkeypatch):
    sent: list[tuple[str, dict]] = []

    async def fake_send(request_id, body):
        sent.append((request_id, body))
        return True, 1, None

    monkeypatch.setattr("src.services.testing_client.send_stand_setup_completed", fake_send)
    return sent


class TestVmStandSetupStart:
    async def test_dispatches_vm_task_with_guest_login_and_script_in_stash(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch, captured_stash,
    ):
        vm = await _held_vm(db, make_hub, make_vm)
        await _prepared_on_snapshot(db, vm, "1.8.1.6")

        resp = await client.post(
            f"{BASE}/{vm.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_vss_1:step1", "requested_by_department_id": "dep_a",
                  "stand_setup": SETUP, "provisioning": PROVISIONING},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "in_progress" and body["stand_setup_request_id"].startswith("ssr_")

        [task] = captured_dispatch
        assert task["task_kind"] == "vm.stand_setup"
        assert task["target_server_id"] == vm.hub_server_id
        payload = task["payload"]
        assert payload["vm_id"] == vm.id
        assert payload["guest_ip"] == "10.9.1.5"
        assert payload["stand_setup"]["kernel_cmdline_extra"] == ["audit=0"]
        assert payload["provisioning"]["degraded_reboot_attempts"] == 3
        # Ни скрипт, ни пароль входа в payload (и в таблицу tasks) не попадают.
        assert "s3cr3t" not in str(payload) and "snap-pass" not in str(payload)
        stash = captured_stash[payload["stash_key"]]
        assert stash["guest_login_user"] == "snapuser" and stash["guest_password"] == "snap-pass"
        assert stash["stand_setup_script"] == SETUP["script"]

        row = (await db.execute(select(ServerStandSetupRequest).where(
            ServerStandSetupRequest.id == body["stand_setup_request_id"],
        ))).scalar_one()
        assert (row.server_id, row.vm_id) == (None, vm.id)
        assert "script" not in row.stand_setup

    async def test_idempotent_by_correlation_id(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch, captured_stash,
    ):
        vm = await _held_vm(db, make_hub, make_vm, ip="10.9.1.6")
        first = await client.post(
            f"{BASE}/{vm.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_vss_2:step1", "stand_setup": SETUP},
        )
        again = await client.post(
            f"{BASE}/{vm.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_vss_2:step1", "stand_setup": SETUP},
        )
        assert first.status_code == again.status_code == 202
        assert again.json()["stand_setup_request_id"] == first.json()["stand_setup_request_id"]
        assert [c["task_kind"] for c in captured_dispatch] == ["vm.stand_setup"]

    async def test_vm_not_booked_is_rejected(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch, captured_stash,
    ):
        vm = await _held_vm(db, make_hub, make_vm, holder=None, ip="10.9.1.7")
        resp = await client.post(
            f"{BASE}/{vm.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_vss_3:step1", "stand_setup": SETUP},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "VM_NOT_BUSY"
        assert captured_dispatch == []
        rows = (await db.execute(select(ServerStandSetupRequest).where(
            ServerStandSetupRequest.correlation_id == "qi_vss_3:step1",
        ))).scalars().all()
        assert rows == []

    async def test_vm_booked_by_other_service_is_rejected(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch, captured_stash,
    ):
        vm = await _held_vm(db, make_hub, make_vm, holder="acs", ip="10.9.1.8")
        resp = await client.post(
            f"{BASE}/{vm.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_vss_4:step1", "stand_setup": SETUP},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "VM_RESERVED_BY_OTHER"
        assert captured_dispatch == []

    async def test_foreign_department_masked_as_404(
        self, client, db, make_hub, make_vm, configure_service_keys, captured_dispatch, captured_stash,
    ):
        vm = await _held_vm(db, make_hub, make_vm, ip="10.9.1.9")
        resp = await client.post(
            f"{BASE}/{vm.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_vss_5:step1", "requested_by_department_id": "dep_b", "stand_setup": SETUP},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "VM_NOT_FOUND"


class TestVmStandSetupCallback:
    async def test_worker_outcome_forwarded_to_testing_service(
        self, client, db, make_hub, make_vm, worker_bot_token_a,
        configure_service_keys, captured_dispatch, captured_stash, sent_callbacks,
    ):
        vm = await _held_vm(db, make_hub, make_vm, ip="10.9.1.10")
        start = await client.post(
            f"{BASE}/{vm.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_vss_6:step1", "stand_setup": SETUP},
        )
        request_id = start.json()["stand_setup_request_id"]
        url = f"{BASE}/{vm.id}/stand-setup-done"
        failure = {"stand_setup_request_id": request_id, "succeeded": False,
                   "failed_step": "reboot_verify", "error": "guest did not come back"}

        no_header = await client.post(url, headers=_user_hdr(worker_bot_token_a), json=failure)
        assert no_header.status_code == 403

        resp = await client.post(
            url, headers={**_user_hdr(worker_bot_token_a), "X-Target-Department-Id": "dep_a"}, json=failure,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True, "status": "failed", "callback_delivered": True}
        # Тело — то же, что у серверного stand-setup.
        assert sent_callbacks == [(request_id, {
            "correlation_id": "qi_vss_6:step1", "succeeded": False,
            "failed_step": "reboot_verify", "error": "guest did not come back",
        })]
        row = (await db.execute(select(ServerStandSetupRequest).where(
            ServerStandSetupRequest.id == request_id,
        ).execution_options(populate_existing=True))).scalar_one()
        assert row.status == "failed" and row.callback_delivered_at is not None

    async def test_request_of_other_vm_is_404(
        self, client, db, make_hub, make_vm, worker_bot_token_a,
        configure_service_keys, captured_dispatch, captured_stash, sent_callbacks,
    ):
        vm = await _held_vm(db, make_hub, make_vm, ip="10.9.1.11")
        other = await _held_vm(db, make_hub, make_vm, ip="10.9.1.12")
        start = await client.post(
            f"{BASE}/{vm.id}/stand-setup", headers=_svc_hdr(),
            json={"correlation_id": "qi_vss_7:step1", "stand_setup": SETUP},
        )
        resp = await client.post(
            f"{BASE}/{other.id}/stand-setup-done",
            headers={**_user_hdr(worker_bot_token_a), "X-Target-Department-Id": "dep_a"},
            json={"stand_setup_request_id": start.json()["stand_setup_request_id"], "succeeded": True},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "STAND_SETUP_REQUEST_NOT_FOUND"
        assert sent_callbacks == []
