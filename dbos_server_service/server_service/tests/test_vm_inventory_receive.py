"""Тесты приёма инвентаря гостя ВМ воркером:

* `POST /internal/vms/{id}/inventory` — hardware/guest facts после
  `vm.inventory_sync` (os_version/hostname/kernel + warn-on-drift по vCPU);
* `POST /internal/vms/{id}/users/inventory` — OS-пользователи гостя после
  `vm.users_inventory` (reconcile против привязок `server_account_vms`).

Зеркало серверных inventory-приёмов (`test_internal_callbacks.py` /
`test_users_inventory.py`), но по VM-домену.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from tests._helpers import assert_error, auth_hdr as _hdr, make_emit_capture

BASE_INT = "/api/server/v1/internal"


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(
        monkeypatch,
        "src.services.internal_service.audit_service.emit",
    )


def _by_action(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


@pytest_asyncio.fixture
async def make_hub(db):
    from src.models import Server, ServerDisk
    from src.utils.ids import _new_id, server_id as new_id

    async def _factory(*, department_id: str = "dep_a") -> Server:
        suffix = uuid.uuid4().hex[:6]
        srv = Server(
            id=new_id(),
            hostname=f"hub-{suffix}",
            ip_address=f"10.41.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
            ssh_port=22,
            department_id=department_id,
            is_managed=True,
            is_vms_hub=True,
            virtualization=True,
        )
        db.add(srv)
        await db.flush()
        db.add(ServerDisk(
            id=_new_id("dsk_"), server_id=srv.id,
            device_name="system", size_gb=1000, is_system=True,
        ))
        await db.flush()
        return srv

    return _factory


@pytest_asyncio.fixture
async def make_vm(db):
    from src.models import Vm
    from src.utils.ids import vm_id as new_id

    async def _factory(
        *, hub, department_id: str = "dep_a", name: str | None = None,
        cpu: int = 4, os_version: str | None = None,
        mgmt_user: str | None = None,
    ) -> Vm:
        vm = Vm(
            id=new_id(),
            name=name or f"vm-{uuid.uuid4().hex[:6]}",
            hub_server_id=hub.id,
            department_id=department_id,
            cpu=cpu, ram_mb=8192, disk_gb=100,
            os_version=os_version,
            mgmt_user=mgmt_user,
        )
        db.add(vm)
        await db.flush()
        await db.refresh(vm)
        return vm

    return _factory


@pytest_asyncio.fixture
async def make_account_on_vm(db):
    from src.models import ServerAccount, ServerAccountVm
    from src.utils.ids import server_account_id, server_account_vm_id

    async def _factory(
        *, vm, login: str, department_id: str = "dep_a",
        has_sudo: bool = False, unix_groups: list[str] | None = None,
        shell: str | None = None, present_on_vm: bool = True,
    ) -> ServerAccount:
        acc = ServerAccount(
            id=server_account_id(),
            department_id=department_id,
            login=login,
            has_sudo=has_sudo,
            unix_groups=unix_groups or [],
            shell=shell,
        )
        db.add(acc)
        await db.flush()
        db.add(ServerAccountVm(
            id=server_account_vm_id(),
            account_id=acc.id,
            vm_id=vm.id,
            login=login,
            present_on_vm=present_on_vm,
        ))
        await db.flush()
        return acc

    return _factory


def _inv_payload(**over) -> dict:
    base = {
        "hostname": "vm-guest",
        "kernel": "5.15.0-astra-amd64",
        "cpu_cores": 4,
        "os_version": "1.8.1.6",
        "disks": [],
    }
    base.update(over)
    return base


# ── inventory приём ──────────────────────────────────────────────────────────


class TestVmInventoryReceive:
    async def test_worker_bot_writes_guest_facts(
        self, client, worker_bot_token_a, make_hub, make_vm, db,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_inv_payload(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["os_version"] == "1.8.1.6"
        assert body["os_changed"] is False
        assert body["drift_fields"] == []

        from src.models import Vm
        refreshed = (await db.execute(select(Vm).where(Vm.id == vm.id))).scalar_one()
        assert refreshed.os_version == "1.8.1.6"
        assert refreshed.hostname == "vm-guest"
        assert refreshed.kernel == "5.15.0-astra-amd64"
        assert refreshed.os_last_synced_at is not None

    async def test_os_version_change_sets_os_changed(
        self, client, worker_bot_token_a, make_hub, make_vm, db,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub, os_version="1.7.5.9")
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_inv_payload(os_version="1.8.1.6"),
        )
        assert resp.status_code == 200
        assert resp.json()["os_changed"] is True
        from src.models import Vm
        refreshed = (await db.execute(select(Vm).where(Vm.id == vm.id))).scalar_one()
        # box→DB: версия перезаписана.
        assert refreshed.os_version == "1.8.1.6"

    async def test_cpu_drift_not_overwritten_warns(
        self, client, worker_bot_token_a, make_hub, make_vm, db, captured_emits,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub, cpu=4)
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_inv_payload(cpu_cores=8),
        )
        assert resp.status_code == 200
        assert resp.json()["drift_fields"] == ["cpu"]
        # Конфигурация ВМ НЕ перетёрта.
        from src.models import Vm
        refreshed = (await db.execute(select(Vm).where(Vm.id == vm.id))).scalar_one()
        assert refreshed.cpu == 4
        drift = _by_action(captured_emits, "vm.inventory_drift_detected")
        assert len(drift) == 1
        assert drift[0]["details"]["drift"]["cpu"] == {"old": 4, "new": 8}

    async def test_inventory_received_audit_emitted(
        self, client, worker_bot_token_a, make_hub, make_vm, captured_emits,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        await client.post(
            f"{BASE_INT}/vms/{vm.id}/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_inv_payload(),
        )
        got = _by_action(captured_emits, "vm.inventory_received")
        assert len(got) == 1
        assert got[0]["status"] == "success"

    async def test_missing_dept_header_403(
        self, client, worker_bot_token_a, make_hub, make_vm,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/inventory",
            headers=_hdr(worker_bot_token_a),  # без X-Target-Department-Id
            json=_inv_payload(),
        )
        assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")

    async def test_wrong_dept_header_404(
        self, client, worker_bot_token_a, make_hub, make_vm,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_b"),
            json=_inv_payload(),
        )
        assert_error(resp, 404, "VM_NOT_FOUND")

    async def test_vm_not_found_404(
        self, client, worker_bot_token_a,
    ):
        resp = await client.post(
            f"{BASE_INT}/vms/vm_missing/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_inv_payload(),
        )
        assert_error(resp, 404, "VM_NOT_FOUND")


# ── users inventory приём ────────────────────────────────────────────────────


def _users_payload(*users: dict) -> dict:
    return {"users": list(users)}


def _user(login: str, **over) -> dict:
    base = {"login": login, "uid": 1000, "shell": "/bin/bash",
            "home_dir": f"/home/{login}", "unix_groups": [], "has_sudo": False}
    base.update(over)
    return base


class TestVmUsersInventoryReceive:
    async def test_present_and_attribute_drift(
        self, client, worker_bot_token_a, make_hub, make_vm,
        make_account_on_vm, db, captured_emits,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_account_on_vm(
            vm=vm, login="alice", has_sudo=False, unix_groups=["users"],
            shell="/bin/bash",
        )
        # Гость: alice есть, но с sudo и в другой группе → attribute drift.
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/users/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_users_payload(
                _user("alice", has_sudo=True, unix_groups=["users", "sudo"]),
            ),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["present"] == 1
        assert body["drifted"] == 1
        assert len(body["diffs"]) == 1
        assert body["diffs"][0]["account_id"] == acc.id
        assert set(body["diffs"][0]["fields"].keys()) == {"has_sudo", "unix_groups"}
        # БД не перетёрта.
        from src.models import ServerAccount
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.has_sudo is False
        assert refreshed.unix_groups == ["users"]
        assert _by_action(captured_emits, "vm.account_drift_detected")

    async def test_missing_on_box_flips_present_flag(
        self, client, worker_bot_token_a, make_hub, make_vm,
        make_account_on_vm, db, captured_emits,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        acc = await make_account_on_vm(vm=vm, login="bob", present_on_vm=True)
        # Гость пуст → bob пропал.
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/users/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_users_payload(),
        )
        assert resp.status_code == 200
        assert resp.json()["drifted"] == 1
        from src.models import ServerAccountVm
        link = (await db.execute(
            select(ServerAccountVm).where(ServerAccountVm.account_id == acc.id)
        )).scalar_one()
        assert link.present_on_vm is False
        miss = _by_action(captured_emits, "vm.account_drift_detected")
        assert any(e["details"]["drift"] == "missing_on_box" for e in miss)

    async def test_unknown_user_reported_not_created(
        self, client, worker_bot_token_a, make_hub, make_vm,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/users/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_users_payload(_user("ghost", uid=1500)),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] == 0
        assert [u["login"] for u in body["unknown_users"]] == ["ghost"]

    async def test_unlinked_existing_dept_account(
        self, client, worker_bot_token_a, make_hub, make_vm, db,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub)
        # Аккаунт того же отдела с login=carol, но НЕ привязан к этой ВМ.
        from src.models import ServerAccount
        from src.utils.ids import server_account_id
        carol = ServerAccount(
            id=server_account_id(), department_id="dep_a", login="carol",
        )
        db.add(carol)
        await db.flush()
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/users/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_users_payload(_user("carol")),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["unknown_users"] == []
        assert [u["login"] for u in body["unlinked_existing"]] == ["carol"]
        assert body["unlinked_existing"][0]["candidates"][0]["account_id"] == carol.id

    async def test_mgmt_user_ignored(
        self, client, worker_bot_token_a, make_hub, make_vm,
    ):
        hub = await make_hub()
        vm = await make_vm(hub=hub, mgmt_user="dbos")
        resp = await client.post(
            f"{BASE_INT}/vms/{vm.id}/users/inventory",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json=_users_payload(_user("dbos", uid=2000)),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Управляющая учётка ВМ не классифицируется как unknown.
        assert body["unknown_users"] == []
        assert body["drifted"] == 0
