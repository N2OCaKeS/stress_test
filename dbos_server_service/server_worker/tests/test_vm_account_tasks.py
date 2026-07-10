"""Тесты worker-тасок учёток общего пула в гостях ВМ:
`vm.account_provision` / `vm.account_update_on_host` / `vm.account_deprovision`.

SSH мокается `_FakeSshClient` (дефолт rc=0). Ассертим состав guest-команд
(useradd/usermod/userdel/chpasswd) и что провижн тянет пароль через internal.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.models import Task
from src.tasks import _vm_prepare_helpers, vms, vms_accounts

_MGMT = {
    "management_user": "dbos",
    "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc123 dbos@vm",
    "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n",
    "password": "S3cretPass",
}


@pytest.fixture
def stub_mgmt(monkeypatch):
    """Замокать чтение mgmt-материала из stash'а для managed-пути (ключ)."""
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
    """Замокать open_hub_session (отдаёт fake из holder) для vms_accounts."""
    holder: dict = {"ssh": None}

    async def _open(payload):  # noqa: ARG001
        fake = holder["ssh"]
        return fake, fake.host

    monkeypatch.setattr(vms_accounts, "open_hub_session", _open)
    return holder


async def _single_attempt(tid: str):
    async with AsyncSessionLocal() as session:
        await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
        await session.commit()


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
        "login": "app",
        "account_id": "acc_1",
        "target_department_id": "dep1",
    }
    payload.update(extra)
    return payload


class TestVmAccountProvision:
    async def test_provision_useradd_and_password(
        self, make_task, fetch_task, stub_session, monkeypatch,
    ):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake

        async def _fetch_by_id(account_id, target_dept=None):  # noqa: ARG001
            return {"password": "guestpw1"}

        monkeypatch.setattr(
            vms.server_service_client, "fetch_account_password_by_id", _fetch_by_id,
        )
        tid = await make_task(
            task_kind="vm.account_provision", target_server_id="hub1",
            payload=_base_payload(has_sudo=True, unix_groups=["docker"]),
        )
        await vms_accounts.vm_account_provision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # useradd для гостевой учётки (внутри вложенного guest_ssh к 10.0.0.50).
        assert any("useradd" in c and "app" in c and "10.0.0.50" in c for c in cmds)
        assert any("chpasswd" in c and "guestpw1" in c for c in cmds)
        assert any("usermod -aG" in c and "docker" in c for c in cmds)


class TestVmAccountUpdate:
    async def test_update_usermod_groups(self, make_task, fetch_task, stub_session):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake
        tid = await make_task(
            task_kind="vm.account_update_on_host", target_server_id="hub1",
            payload=_base_payload(has_sudo=True, unix_groups=["wheel"]),
        )
        await vms_accounts.vm_account_update_on_host.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any(
            "usermod -aG" in c and "wheel" in c and "sudo" in c and "app" in c
            for c in fake.commands
        )


class TestVmAccountDeprovision:
    async def test_deprovision_userdel(self, make_task, fetch_task, stub_session):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake
        tid = await make_task(
            task_kind="vm.account_deprovision", target_server_id="hub1",
            payload=_base_payload(remove_home=True),
        )
        await vms_accounts.vm_account_deprovision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert any("userdel -r app" in c and "10.0.0.50" in c for c in fake.commands)


class TestManagedGuestKey:
    """Managed-ВМ (`creds_stash_key` в payload): guest-шаги идут по ключу
    управляющего пользователя, а не по `sshpass`; временный ключ пишется и
    затирается на hub'е."""

    async def test_provision_uses_key_connector(
        self, make_task, fetch_task, stub_session, stub_mgmt, monkeypatch,
    ):
        fake = _FakeSshClient()
        fake.set_response("mktemp", 0, "/tmp/dbos-key")
        stub_session["ssh"] = fake

        async def _fetch_by_id(account_id, target_dept=None):  # noqa: ARG001
            return {"password": "guestpw1"}

        monkeypatch.setattr(
            vms.server_service_client, "fetch_account_password_by_id", _fetch_by_id,
        )
        tid = await make_task(
            task_kind="vm.account_provision", target_server_id="hub1",
            payload=_base_payload(
                has_sudo=True, unix_groups=["docker"],
                creds_stash_key="dbos:dispatch_creds:abc",
            ),
        )
        await vms_accounts.vm_account_provision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # useradd идёт по ключу управляющего пользователя (не sshpass).
        assert any(
            "useradd" in c and "ssh -i /tmp/dbos-key" in c and "dbos@10.0.0.50" in c
            for c in cmds
        )
        assert not any("sshpass" in c for c in cmds)
        # временный ключ затёрт на hub'е.
        assert any("shred -u /tmp/dbos-key" in c for c in cmds)

    async def test_deprovision_uses_key_connector(
        self, make_task, fetch_task, stub_session, stub_mgmt,
    ):
        fake = _FakeSshClient()
        fake.set_response("mktemp", 0, "/tmp/dbos-key")
        stub_session["ssh"] = fake
        tid = await make_task(
            task_kind="vm.account_deprovision", target_server_id="hub1",
            payload=_base_payload(
                remove_home=True, creds_stash_key="dbos:dispatch_creds:abc",
            ),
        )
        await vms_accounts.vm_account_deprovision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        assert any(
            "userdel -r app" in c and "ssh -i /tmp/dbos-key" in c and "dbos@10.0.0.50" in c
            for c in cmds
        )
        assert not any("sshpass" in c for c in cmds)
        assert any("shred -u /tmp/dbos-key" in c for c in cmds)
