"""Тесты worker-тасок инвентаризации гостя ВМ: `vm.inventory_sync` /
`vm.users_inventory`.

Тот же общий код, что и на сервере (`_inventory_common`), но цель — гость ВМ
через hub. SSH мокается `_FakeSshClient` (ответы по подстроке команды). Ассертим
guest-hop wiring (команды идут вложенным ssh в гостя), submit обратно в
server_service и что submit-фейл не роняет task'у.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.core.exceptions import CredentialFetchError
from src.tasks import _vm_prepare_helpers, vms_inventory

_MGMT = {
    "management_user": "dbos",
    "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc dbos@vm",
    "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n",
    "password": "S3cretPass",
}

_PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "daemon:x:1:1::/usr/sbin:/usr/sbin/nologin\n"
    "ops:x:1001:1001:Ops:/home/ops:/bin/bash\n"
    "deploy:x:1002:1002::/home/deploy:/bin/sh\n"
    "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
)
_GROUP = "root:x:0:\nsudo:x:27:ops\nops:x:1001:\ndeploy:x:1002:\n"
_LOGIN_DEFS = "# defaults\nUID_MIN\t1000\nUID_MAX\t60000\n"


@pytest.fixture
def stub_mgmt(monkeypatch):
    async def _read_mgmt(stash_key):  # noqa: ARG001
        return dict(_MGMT)
    monkeypatch.setattr(_vm_prepare_helpers, "_read_mgmt_install", _read_mgmt)


class _FakeSshClient:
    def __init__(self, host: str = "10.0.0.7"):
        self.host = host
        self._responses: list[tuple[str, tuple[int, str, str]]] = []
        self.commands: list[str] = []

    def set_response(self, pat: str, rc: int, stdout: str = "", stderr: str = ""):
        self._responses.append((pat, (rc, stdout, stderr)))

    async def connect(self):
        return None

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def run(self, command, *, sudo=False, stdin_payload=None):  # noqa: ARG002
        self.commands.append(command)
        for pat, resp in self._responses:
            if pat in command:
                return resp
        return (0, "", "")


@pytest.fixture
def stub_session(monkeypatch):
    holder: dict = {"ssh": None}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    monkeypatch.setattr(vms_inventory, "open_hub_session", _open)
    return holder


def _base_payload(**extra) -> dict:
    payload = {
        "vm_id": "vm_1",
        "vm_name": "vm-test",
        "hub_server_id": "hub1",
        "server_id": "hub1",
        "host": "10.0.0.7",
        "is_managed": True,
        "management_user": "dbos",
        "guest_ip": "10.0.0.50",
        "target_department_id": "dep1",
    }
    payload.update(extra)
    return payload


def _inventory_fake() -> _FakeSshClient:
    fake = _FakeSshClient()
    fake.set_response("hostname", 0, "vm-guest\n")
    fake.set_response("uname -a", 0, "Linux vm-guest 6.1.0 x86_64\n")
    fake.set_response("os-release", 0, 'NAME="Astra Linux"\nVERSION_ID="1.7"\n')
    fake.set_response("build_version", 0, "1.7.9\n")
    fake.set_response("astra_license", 0, "лицензия ... Орёл ...\n")
    return fake


class TestVmInventorySync:
    async def test_collects_and_submits(
        self, make_task, fetch_task, stub_session, monkeypatch,
    ):
        fake = _inventory_fake()
        stub_session["ssh"] = fake

        submit_calls = []

        async def fake_submit(vm_id, payload, target_department_id=None):
            submit_calls.append((vm_id, payload, target_department_id))
            return {"ok": True}

        monkeypatch.setattr(
            vms_inventory.server_service_client,
            "submit_vm_inventory_facts", fake_submit,
        )

        tid = await make_task(
            task_kind="vm.inventory_sync", target_server_id="hub1",
            payload=_base_payload(),
        )
        await vms_inventory.vm_inventory_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["vm_id"] == "vm_1"
        assert t.result["submit_status"] == "submitted"
        # Команды инвентаря ушли вложенным ssh в гостя (по паролю u/1 — unmanaged).
        assert any("hostname" in c and "10.0.0.50" in c for c in fake.commands)
        assert len(submit_calls) == 1
        vm_id, payload, dept = submit_calls[0]
        assert vm_id == "vm_1"
        assert dept == "dep1"
        assert payload["hostname"] == "vm-guest"
        # Astra: os_version — версия сборки.
        assert payload["os_version"] == "1.7.9"

    async def test_managed_uses_key_and_shreds(
        self, make_task, fetch_task, stub_session, stub_mgmt, monkeypatch,
    ):
        fake = _inventory_fake()
        fake.set_response("mktemp", 0, "/tmp/dbos-key")
        stub_session["ssh"] = fake

        async def fake_submit(vm_id, payload, target_department_id=None):  # noqa: ARG001
            return {"ok": True}

        monkeypatch.setattr(
            vms_inventory.server_service_client,
            "submit_vm_inventory_facts", fake_submit,
        )

        tid = await make_task(
            task_kind="vm.inventory_sync", target_server_id="hub1",
            payload=_base_payload(creds_stash_key="dbos:dispatch_creds:abc"),
        )
        await vms_inventory.vm_inventory_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # Инвентарь снят по ключу управляющего пользователя (не sshpass).
        assert any("ssh -i /tmp/dbos-key" in c and "dbos@10.0.0.50" in c for c in cmds)
        assert not any("sshpass" in c for c in cmds)
        # Временный ключ затёрт на hub'е.
        assert any("shred -u /tmp/dbos-key" in c for c in cmds)

    async def test_submit_failure_does_not_fail_task(
        self, make_task, fetch_task, stub_session, monkeypatch,
    ):
        fake = _inventory_fake()
        stub_session["ssh"] = fake

        async def boom(*a, **kw):
            raise CredentialFetchError(
                error_code="VM_INVENTORY_SUBMIT_REJECTED",
                message="server_service returned 404",
            )

        monkeypatch.setattr(
            vms_inventory.server_service_client,
            "submit_vm_inventory_facts", boom,
        )

        tid = await make_task(
            task_kind="vm.inventory_sync", target_server_id="hub1",
            payload=_base_payload(),
        )
        await vms_inventory.vm_inventory_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["submit_status"] == "submit_failed:VM_INVENTORY_SUBMIT_REJECTED"
        assert "hostname" in t.result["facts"]


class TestVmUsersInventory:
    async def test_collects_and_submits(
        self, make_task, fetch_task, stub_session, monkeypatch,
    ):
        fake = _FakeSshClient()
        fake.set_response("getent passwd", 0, _PASSWD)
        fake.set_response("getent group", 0, _GROUP)
        fake.set_response("login.defs", 0, _LOGIN_DEFS)
        stub_session["ssh"] = fake

        submit_calls = []

        async def fake_submit(vm_id, payload, target_department_id=None):
            submit_calls.append((vm_id, payload))
            return {"ok": True, "created": 0, "present": 2, "drifted": 0, "diffs": []}

        monkeypatch.setattr(
            vms_inventory.server_service_client,
            "submit_vm_users_inventory", fake_submit,
        )

        tid = await make_task(
            task_kind="vm.users_inventory", target_server_id="hub1",
            payload=_base_payload(),
        )
        await vms_inventory.vm_users_inventory.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["user_count"] == 2
        assert t.result["submit_status"] == "submitted"
        assert t.result["reconcile_summary"] == {
            "created": 0, "present": 2, "drifted": 0,
        }
        assert len(submit_calls) == 1
        assert {u["login"] for u in submit_calls[0][1]["users"]} == {"ops", "deploy"}

    async def test_users_not_in_audit(
        self, make_task, fetch_task, captured_audit, stub_session, monkeypatch,
    ):
        fake = _FakeSshClient()
        fake.set_response("getent passwd", 0, _PASSWD)
        fake.set_response("getent group", 0, _GROUP)
        fake.set_response("login.defs", 0, _LOGIN_DEFS)
        stub_session["ssh"] = fake

        async def fake_submit(*a, **kw):
            return {"ok": True}

        monkeypatch.setattr(
            vms_inventory.server_service_client,
            "submit_vm_users_inventory", fake_submit,
        )

        tid = await make_task(
            task_kind="vm.users_inventory", target_server_id="hub1",
            payload=_base_payload(),
        )
        await vms_inventory.vm_users_inventory.original_func(tid)

        assert captured_audit, "audit must be emitted"
        emitted = captured_audit[0]["details"].get("result", {})
        assert "users" not in emitted
        assert emitted.get("vm_id") == "vm_1"
        assert emitted.get("user_count") == 2
