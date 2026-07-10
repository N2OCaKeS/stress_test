"""Тесты worker-тасок мутации пакетов гостя ВМ: `vm.{install,remove,update}_packages`.

Та же общая логика мутации, что и на сервере (`_packages_common.mutate_packages`),
но цель — гость ВМ через hub. SSH мокается `_FakeSshClient` (ответы по подстроке
команды). Ассертим guest-hop wiring (команды идут вложенным ssh в гостя под sudo),
контракт результата и что битое имя/non-zero валят task'у.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import _vm_prepare_helpers, vms_packages

_MGMT = {
    "management_user": "dbos",
    "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc dbos@vm",
    "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n",
    "password": "S3cretPass",
}


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

    monkeypatch.setattr(vms_packages, "open_hub_session", _open)
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
        # os_family-хинт → dpkg без живого детекта (как у vm.list_packages).
        "os_family": "apt",
        "target_department_id": "dep1",
    }
    payload.update(extra)
    return payload


async def _force_single_attempt(tid: str) -> None:
    """Снять durable-retry: max_attempts=1, чтобы ошибка сразу давала FAILED."""
    from sqlalchemy import update
    from src.db.session import AsyncSessionLocal
    from src.models import Task
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Task).where(Task.id == tid).values(max_attempts=1)
        )
        await session.commit()


class TestVmInstallPackages:
    async def test_install_success_guest_hop_under_sudo(
        self, make_task, fetch_task, captured_audit, stub_session,
    ):
        fake = _FakeSshClient()
        fake.set_response("apt-get install -y sl", 0, "Setting up sl ...\n")
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="vm.install_packages", target_server_id="hub1",
            payload=_base_payload(packages=["sl"], operation="install"),
        )
        await vms_packages.vm_install_packages.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["operation"] == "install"
        assert t.result["package_manager"] == "dpkg"
        assert t.result["packages"] == ["sl"]
        assert t.result["returncode"] == 0
        # Команда ушла вложенным ssh в гостя (адрес гостя в строке) под sudo.
        assert any(
            "apt-get install -y sl" in c and "10.0.0.50" in c and "sudo" in c
            for c in fake.commands
        )
        # Audit несёт интент оператора (operation/packages), без полного stdout.
        details = captured_audit[0]["details"].get("result", {})
        assert details.get("operation") == "install"
        assert details.get("packages") == ["sl"]

    async def test_managed_uses_key_and_shreds(
        self, make_task, fetch_task, stub_session, stub_mgmt,
    ):
        fake = _FakeSshClient()
        fake.set_response("mktemp", 0, "/tmp/dbos-key")
        fake.set_response("apt-get install -y htop", 0, "Setting up htop ...\n")
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="vm.install_packages", target_server_id="hub1",
            payload=_base_payload(
                packages=["htop"], operation="install",
                creds_stash_key="dbos:dispatch_creds:abc",
            ),
        )
        await vms_packages.vm_install_packages.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = fake.commands
        # Мутация выполнена по ключу управляющего пользователя (не sshpass).
        assert any("ssh -i /tmp/dbos-key" in c and "dbos@10.0.0.50" in c for c in cmds)
        assert not any("sshpass" in c for c in cmds)
        # Временный ключ затёрт на hub'е.
        assert any("shred -u /tmp/dbos-key" in c for c in cmds)

    async def test_invalid_name_fails_before_apt(
        self, make_task, fetch_task, stub_session,
    ):
        fake = _FakeSshClient()
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="vm.install_packages", target_server_id="hub1",
            payload=_base_payload(packages=["sl; rm -rf /"], operation="install"),
        )
        await _force_single_attempt(tid)
        await vms_packages.vm_install_packages.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert not any("apt-get" in c for c in fake.commands)


class TestVmRemoveUpdate:
    async def test_remove_success(self, make_task, fetch_task, stub_session):
        fake = _FakeSshClient()
        fake.set_response("apt-get remove -y sl", 0, "Removing sl ...\n")
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="vm.remove_packages", target_server_id="hub1",
            payload=_base_payload(packages=["sl"], operation="remove"),
        )
        await vms_packages.vm_remove_packages.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["operation"] == "remove"
        assert any("apt-get remove -y sl" in c for c in fake.commands)

    async def test_update_all_no_packages(self, make_task, fetch_task, stub_session):
        fake = _FakeSshClient()
        fake.set_response("apt-get upgrade -y", 0, "0 upgraded\n")
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="vm.update_packages", target_server_id="hub1",
            payload=_base_payload(packages=[], operation="update"),
        )
        await vms_packages.vm_update_packages.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["operation"] == "update"
        assert t.result["packages"] == []
        assert any("apt-get upgrade -y" in c for c in fake.commands)

    async def test_command_nonzero_fails(self, make_task, fetch_task, stub_session):
        fake = _FakeSshClient()
        fake.set_response(
            "apt-get install -y nosuchpkg", 100, "", "E: Unable to locate package",
        )
        stub_session["ssh"] = fake

        tid = await make_task(
            task_kind="vm.install_packages", target_server_id="hub1",
            payload=_base_payload(packages=["nosuchpkg"], operation="install"),
        )
        await _force_single_attempt(tid)
        await vms_packages.vm_install_packages.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
