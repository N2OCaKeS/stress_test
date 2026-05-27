"""Тесты SSH user-lifecycle (useradd/usermod/userdel) + provision-таски (#13).

* `SshClient.create_user` / `modify_user` / `delete_user` — happy path,
  idempotency (уже существует / не существует), инъекция в login/группах;
* end-to-end handlers `account.provision` / `account.update_on_host` /
  `account.deprovision`: fetch creds → SSH → submit_provision_status.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from src.clients.ssh import SshClient, SshError
from src.core.constants import TaskStatus
from src.tasks import users


def _run_result(stdout="", stderr="", rc=0):
    res = MagicMock()
    res.stdout = stdout
    res.stderr = stderr
    res.exit_status = rc
    return res


def _conn(run_results):
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(side_effect=run_results)
    return conn


# ── SshClient.create_user ─────────────────────────────────────────────────────


class TestCreateUser:
    async def test_useradd_new_user_then_chpasswd(self, monkeypatch):
        # getent passwd (not found rc=2) → useradd (rc=0) → chpasswd (rc=0)
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "sess-pwd") as ssh:
            await ssh.create_user(
                "deploy", new_password="NewPass!42",
                groups=["devs"], has_sudo=True, shell="/bin/bash",
                home_dir="/home/deploy",
            )
        # useradd-команда содержит ожидаемые опции и login.
        useradd_cmd = conn.run.await_args_list[1].args[0]
        assert "useradd" in useradd_cmd
        assert "-m" in useradd_cmd
        assert "-s /bin/bash" in useradd_cmd
        assert "-d /home/deploy" in useradd_cmd
        assert "-G devs,sudo" in useradd_cmd
        assert useradd_cmd.rstrip().endswith("deploy")
        # chpasswd получил новый пароль на stdin, не в команде.
        chpasswd_stdin = conn.run.await_args_list[2].kwargs["input"]
        assert "deploy:NewPass!42\n" in chpasswd_stdin

    async def test_useradd_idempotent_when_exists(self, monkeypatch):
        # getent passwd (found rc=0) → modify_user usermod (rc=0) → chpasswd (rc=0)
        conn = _conn([
            _run_result("deploy:x:1001:1001::/home/deploy:/bin/bash", "", 0),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "sess-pwd") as ssh:
            await ssh.create_user(
                "deploy", new_password="NewPass!42", has_sudo=True,
            )
        # useradd НЕ должен вызываться — пользователь уже есть, идём в usermod.
        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert not any("useradd" in c for c in cmds)
        assert any("usermod" in c for c in cmds)

    async def test_useradd_nonzero_raises(self, monkeypatch):
        conn = _conn([
            _run_result("", "", 2),     # getent: not found
            _run_result("", "boom", 1),  # useradd fails
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        with pytest.raises(SshError) as ei:
            async with SshClient("h", "ops", "p") as ssh:
                await ssh.create_user("deploy")
        assert ei.value.error_code == "SSH_USERADD_FAILED"

    async def test_invalid_login_rejected(self, monkeypatch):
        conn = _conn([_run_result("", "", 0)])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        with pytest.raises(SshError) as ei:
            async with SshClient("h", "ops", "p") as ssh:
                await ssh.create_user("bad; rm -rf /")
        assert ei.value.error_code == "SSH_INVALID_LOGIN"

    async def test_invalid_group_rejected(self, monkeypatch):
        conn = _conn([
            _run_result("", "", 2),  # getent not found
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        with pytest.raises(SshError) as ei:
            async with SshClient("h", "ops", "p") as ssh:
                await ssh.create_user("ok", groups=["bad group"])
        assert ei.value.error_code == "SSH_INVALID_ARG"


# ── SshClient.modify_user ──────────────────────────────────────────────────────


class TestModifyUser:
    async def test_usermod_groups_and_shell(self, monkeypatch):
        conn = _conn([_run_result("", "", 0)])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.modify_user("deploy", groups=["devs"], has_sudo=True, shell="/bin/sh")
        cmd = conn.run.await_args_list[0].args[0]
        assert "usermod" in cmd
        assert "-s /bin/sh" in cmd
        assert "-G devs,sudo" in cmd

    async def test_usermod_noop_when_nothing_to_change(self, monkeypatch):
        conn = _conn([_run_result("", "", 0)])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.modify_user("deploy")
        assert conn.run.await_count == 0

    async def test_usermod_nonzero_raises(self, monkeypatch):
        conn = _conn([_run_result("", "no such user", 6)])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        with pytest.raises(SshError) as ei:
            async with SshClient("h", "ops", "p") as ssh:
                await ssh.modify_user("deploy", shell="/bin/sh")
        assert ei.value.error_code == "SSH_USERMOD_FAILED"


# ── SshClient.delete_user ──────────────────────────────────────────────────────


class TestDeleteUser:
    async def test_userdel_happy(self, monkeypatch):
        conn = _conn([
            _run_result("deploy:x:1001:1001::/home/deploy:/bin/bash", "", 0),  # getent found
            _run_result("", "", 0),  # userdel ok
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.delete_user("deploy", remove_home=True)
        del_cmd = conn.run.await_args_list[1].args[0]
        assert "userdel" in del_cmd
        assert "--remove" in del_cmd

    async def test_userdel_idempotent_when_missing(self, monkeypatch):
        conn = _conn([_run_result("", "", 2)])  # getent: not found
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.delete_user("deploy")
        # userdel НЕ вызывается — пользователя нет.
        assert conn.run.await_count == 1

    async def test_userdel_rc6_treated_as_success(self, monkeypatch):
        conn = _conn([
            _run_result("deploy:x:1001:1001::/home:/bin/bash", "", 0),
            _run_result("", "does not exist", 6),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.delete_user("deploy")  # не должно бросить


# ── Handlers ──────────────────────────────────────────────────────────────────


def _fetch_creds(login="ops"):
    async def _f(server_id, account_id, target_department_id=None):
        return {"login": login, "password": "sess-pwd", "host": "10.0.0.5"}
    return _f


class TestProvisionHandlers:
    async def test_provision_collects_and_submits(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_p1",
            payload={
                "server_id": "srv_p1", "account_id": "acc_1", "login": "ops",
                "has_sudo": True, "unix_groups": ["devs"], "shell": "/bin/bash",
                "home_dir": "/home/ops",
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        # getent (not found) → useradd → chpasswd
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(server_id, account_id, operation, present, target_department_id=None):
            submit_calls.append((server_id, account_id, operation, present))
            return {"ok": True, "present_on_server": present}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_provision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["operation"] == "provision"
        assert t.result["present_on_server"] is True
        assert submit_calls == [("srv_p1", "acc_1", "provision", True)]

    async def test_update_on_host_submits_present_true(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.update_on_host", target_server_id="srv_p2",
            payload={
                "server_id": "srv_p2", "account_id": "acc_2", "login": "ops",
                "has_sudo": False, "unix_groups": ["devs"], "shell": "/bin/sh",
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        conn = _conn([_run_result("", "", 0)])  # usermod ok
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(server_id, account_id, operation, present, target_department_id=None):
            submit_calls.append((operation, present))
            return {"ok": True, "present_on_server": present}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_update_on_host.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert submit_calls == [("update", True)]

    async def test_deprovision_submits_present_false(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.deprovision", target_server_id="srv_p3",
            payload={
                "server_id": "srv_p3", "account_id": "acc_3", "login": "ops",
                "remove_home": True,
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        # getent found → userdel ok
        conn = _conn([
            _run_result("ops:x:1001:1001::/home/ops:/bin/bash", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(server_id, account_id, operation, present, target_department_id=None):
            submit_calls.append((operation, present))
            return {"ok": True, "present_on_server": present}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_deprovision.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["present_on_server"] is False
        assert submit_calls == [("deprovision", False)]
        # userdel --remove применён.
        del_cmd = conn.run.await_args_list[1].args[0]
        assert "userdel" in del_cmd and "--remove" in del_cmd

    async def test_password_not_in_audit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_p4",
            payload={"server_id": "srv_p4", "account_id": "acc_4", "login": "ops"},
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_provision.original_func(tid)
        assert "sess-pwd" not in str(captured_audit)
