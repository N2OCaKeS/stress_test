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
from tests._ssh_mock_helpers import run_result as _run_result
from tests._ssh_mock_helpers import sudo_probe_result as _sudo_probe


def _conn(run_results):
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(side_effect=run_results)
    return conn


# ── SshClient.create_user ─────────────────────────────────────────────────────


class TestCreateUser:
    async def test_useradd_new_user_then_chpasswd(self, monkeypatch):
        # getent (rc=2) → sudo -n true → useradd (rc=0) → chpasswd (rc=0)
        conn = _conn([
            _run_result("", "", 2),
            _sudo_probe(),
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
        useradd_cmd = conn.run.await_args_list[2].args[0]
        assert "useradd" in useradd_cmd
        assert "-m" in useradd_cmd
        assert "-s /bin/bash" in useradd_cmd
        assert "-d /home/deploy" in useradd_cmd
        assert "-G devs,sudo" in useradd_cmd
        assert useradd_cmd.rstrip().endswith("deploy")
        # chpasswd получил новый пароль на stdin, не в команде.
        chpasswd_stdin = conn.run.await_args_list[3].kwargs["input"]
        assert "deploy:NewPass!42\n" in chpasswd_stdin

    async def test_useradd_idempotent_when_exists(self, monkeypatch):
        # getent (found rc=0) → sudo -n true → usermod (rc=0) → chpasswd (rc=0)
        conn = _conn([
            _run_result("deploy:x:1001:1001::/home/deploy:/bin/bash", "", 0),
            _sudo_probe(),
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
            _sudo_probe(),              # sudo -n true перед useradd
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
        conn = _conn([_sudo_probe(), _run_result("", "", 0)])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.modify_user("deploy", groups=["devs"], has_sudo=True, shell="/bin/sh")
        cmd = conn.run.await_args_list[1].args[0]
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
        conn = _conn([_sudo_probe(), _run_result("", "no such user", 6)])
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
            _sudo_probe(),           # sudo -n true перед userdel
            _run_result("", "", 0),  # userdel ok
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.delete_user("deploy", remove_home=True)
        del_cmd = conn.run.await_args_list[2].args[0]
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
            _sudo_probe(),
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
        stash_dispatch_creds,
    ):
        # provision требует dispatch-stash в Redis вместо
        # plaintext-полей в payload. Эмулируем то, что server_service
        # делает перед dispatch'ем.
        stash_key = await stash_dispatch_creds(password_plaintext="sess-pwd")
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_p1",
            payload={
                "server_id": "srv_p1", "account_id": "acc_1", "login": "ops",
                "has_sudo": True, "unix_groups": ["devs"], "shell": "/bin/bash",
                "home_dir": "/home/ops",
                "creds_stash_key": stash_key,
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        # getent (not found) → sudo -n true → useradd → chpasswd
        conn = _conn([
            _run_result("", "", 2),
            _sudo_probe(),
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
        conn = _conn([_sudo_probe(), _run_result("", "", 0)])  # sudo -n true → usermod ok
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

    async def test_update_on_host_applies_ssh_key_when_present(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """`account.update_on_host` с `ssh_public_key` в payload раскатывает
        ключ на хост после usermod — путь ротации ключа существующего юзера.
        """
        from src.clients.ssh import _MANAGED_KEY_MARKER

        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIROTATED ops@new"
        tid = await make_task(
            task_kind="account.update_on_host", target_server_id="srv_uk",
            payload={
                "server_id": "srv_uk", "account_id": "acc_uk", "login": "ops",
                "has_sudo": False, "unix_groups": ["devs"], "shell": "/bin/sh",
                "ssh_public_key": pubkey,
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        # modify_user и apply_authorized_key открывают ОТДЕЛЬНЫЕ сессии —
        # пробер sudo -n true прогоняется в каждой:
        # sess1: sudo -n true → usermod; sess2: sudo -n true → authorized_keys.
        conn = _conn([
            _sudo_probe(),
            _run_result("", "", 0),
            _sudo_probe(),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(server_id, account_id, operation, present, target_department_id=None):
            return {"ok": True, "present_on_server": present}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_update_on_host.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED

        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert any("usermod" in c for c in cmds)
        bash_cmd = next(c for c in cmds if "authorized_keys" in c)
        # Managed-ротация: фильтр прежних managed-строк + idempotent append.
        assert f'grep -vF " {_MANAGED_KEY_MARKER}"' in bash_cmd
        assert "grep -qxF" in bash_cmd and ">>" in bash_cmd
        # Ключ с маркером ушёл на stdin, не в команду.
        ak_call = next(
            c for c in conn.run.await_args_list
            if "authorized_keys" in (c.args[0] if c.args else "")
        )
        assert pubkey not in bash_cmd
        assert f"{pubkey} {_MANAGED_KEY_MARKER}" in ak_call.kwargs.get("input", "")

    async def test_update_on_host_no_key_skips_authorized_keys(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Без `ssh_public_key` в payload update идёт только через usermod —
        шаг записи ключа не выполняется (back-compat с текущим dispatch'ем).
        """
        tid = await make_task(
            task_kind="account.update_on_host", target_server_id="srv_nk",
            payload={
                "server_id": "srv_nk", "account_id": "acc_nk", "login": "ops",
                "has_sudo": False, "unix_groups": ["devs"], "shell": "/bin/sh",
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        conn = _conn([_sudo_probe(), _run_result("", "", 0)])  # sudo -n true → usermod only
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_update_on_host.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert not any("authorized_keys" in c for c in cmds)

    async def test_update_on_host_apply_password_runs_chpasswd(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """`apply_password=True` на self-сессии прописывает текущий пароль
        аккаунта через chpasswd — это исполнение apply-пути (после set/rotate
        пароля / ручной apply).
        """
        tid = await make_task(
            task_kind="account.update_on_host", target_server_id="srv_up",
            payload={
                "server_id": "srv_up", "account_id": "acc_up", "login": "ops",
                "has_sudo": False, "unix_groups": ["devs"], "shell": "/bin/sh",
                "apply_password": True,
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        # modify_user и set_account_password открывают ОТДЕЛЬНЫЕ сессии, в
        # каждой password-auth прогоняется пробер sudo -n true:
        # sess1: sudo -n true → usermod; sess2: sudo -n true → chpasswd.
        conn = _conn([
            _sudo_probe(),
            _run_result("", "", 0),
            _sudo_probe(),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_update_on_host.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["password_applied"] is True
        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert any("usermod" in c for c in cmds)
        # chpasswd прогнан, пароль ушёл на stdin (login:pwd), не в argv.
        cp_call = next(
            c for c in conn.run.await_args_list
            if "chpasswd" in (c.args[0] if c.args else "")
        )
        assert "ops:sess-pwd\n" in cp_call.kwargs.get("input", "")
        assert "sess-pwd" not in cp_call.args[0]

    async def test_update_on_host_without_apply_password_skips_chpasswd(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Без `apply_password` update идёт только через usermod: chpasswd не
        зовётся, `password_applied` в результате False (attribute-only fan-out).
        """
        tid = await make_task(
            task_kind="account.update_on_host", target_server_id="srv_nap",
            payload={
                "server_id": "srv_nap", "account_id": "acc_nap", "login": "ops",
                "has_sudo": False, "unix_groups": ["devs"], "shell": "/bin/sh",
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        conn = _conn([_sudo_probe(), _run_result("", "", 0)])  # sudo -n true → usermod
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_update_on_host.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["password_applied"] is False
        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert not any("chpasswd" in c for c in cmds)

    async def test_update_on_host_partial_failure_key_after_password(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Пароль применился, запись ключа упала → задача НЕ succeeded, ошибка
        видна в last_error и audit'е (не молчаливый успех).
        """
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPARTIAL ops@x"
        tid = await make_task(
            task_kind="account.update_on_host", target_server_id="srv_pf",
            payload={
                "server_id": "srv_pf", "account_id": "acc_pf", "login": "ops",
                "has_sudo": False, "unix_groups": ["devs"], "shell": "/bin/sh",
                "apply_password": True, "ssh_public_key": pubkey,
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        # sess1: sudo -n true → usermod(ok); sess2: sudo -n true → chpasswd(ok);
        # sess3: sudo -n true → authorized_keys(rc=1, падение).
        conn = _conn([
            _sudo_probe(),
            _run_result("", "", 0),
            _sudo_probe(),
            _run_result("", "", 0),
            _sudo_probe(),
            _run_result("", "permission denied", 1),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submitted = []
        async def fake_submit(*a, **kw):
            submitted.append(a)
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_update_on_host.original_func(tid)
        t = await fetch_task(tid)
        # Не успех: запись ключа упала после применения пароля.
        assert t.status != TaskStatus.SUCCEEDED
        assert "SSH_AUTHORIZED_KEYS_FAILED" in (t.last_error or "")
        # submit_provision_status(present=True) НЕ должен был уйти — мы упали
        # до него, статус на боксе не подтверждаем.
        assert submitted == []
        # Audit несёт failure для действия update_on_host, не success.
        ev = [e for e in captured_audit if e["action"] == "server_account.update_on_host"]
        assert ev and all(e.get("status") != "success" for e in ev)

    async def test_update_on_host_authorized_key_idempotent_across_runs(
        self, monkeypatch,
    ):
        """Повторная раскатка того же ключа не плодит строк в authorized_keys.

        Эмулируем состояние файла между вызовами: managed-запись фильтрует
        прежние managed-строки и дописывает ключ только если его ещё нет.
        Два прогона `apply_authorized_key` → ровно одна строка с ключом.
        """
        from src.clients.ssh import _MANAGED_KEY_MARKER
        from src.services import ssh_client

        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIDEMPOTENT ops@x"

        class _StatefulConn:
            def __init__(self):
                self.lines: list[str] = []
                self.close = MagicMock()
                self.wait_closed = AsyncMock()

            async def run(self, cmd, *, input=None, check=False):
                if "sudo -n true" in cmd:
                    return _run_result("", "", 0)  # NOPASSWD
                if "authorized_keys" in cmd:
                    key_line = (input or "").rstrip("\n")
                    # Managed-write: снести прежние managed-строки, потом
                    # idempotent append.
                    kept = [
                        ln for ln in self.lines
                        if f" {_MANAGED_KEY_MARKER}" not in ln
                    ]
                    if key_line and key_line not in kept:
                        kept.append(key_line)
                    self.lines = kept
                    return _run_result("", "", 0)
                return _run_result("", "", 0)

        conn = _StatefulConn()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        creds = {"login": "ops", "password": "pwd", "host": "10.0.0.9"}
        for _ in range(2):
            await ssh_client.apply_authorized_key(
                creds, "srv_idem", login="ops", public_key=pubkey,
            )

        # Ровно одна строка, и это наш ключ с managed-маркером.
        assert conn.lines == [f"{pubkey} {_MANAGED_KEY_MARKER}"]

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
        # getent found → sudo -n true → userdel ok
        conn = _conn([
            _run_result("ops:x:1001:1001::/home/ops:/bin/bash", "", 0),
            _sudo_probe(),
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
        del_cmd = conn.run.await_args_list[2].args[0]
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


# ── SSH-key bootstrap (F23-B): writing public_key + force_replace ────────────


_ED25519_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY dbos-account"


class TestAuthorizedKeyWrite:
    """`SshClient.create_user(public_key=...)` пишет ключ в authorized_keys.

    Идемпотентно (`force_replace=False`, default) или с перезаписью
    (`force_replace=True`, re-provision после переустановки ОС).
    """

    async def test_new_user_with_key_appends(self, monkeypatch):
        # getent (not found) → sudo -n true → useradd → bash setup authorized_keys
        conn = _conn([
            _run_result("", "", 2),
            _sudo_probe(),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.create_user("deploy", public_key=_ED25519_PUB)
        # Последний run — bash-команда с grep|append (не truncate).
        bash_cmd = conn.run.await_args_list[3].args[0]
        assert "authorized_keys" in bash_cmd
        assert "grep -qxF" in bash_cmd
        assert ">>" in bash_cmd
        # Ключ ушёл на stdin, не argv.
        stdin = conn.run.await_args_list[3].kwargs["input"]
        assert _ED25519_PUB in stdin
        assert _ED25519_PUB not in bash_cmd

    async def test_new_user_force_replace_truncates(self, monkeypatch):
        conn = _conn([
            _run_result("", "", 2),
            _sudo_probe(),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.create_user(
                "deploy", public_key=_ED25519_PUB, force_replace=True,
            )
        bash_cmd = conn.run.await_args_list[3].args[0]
        # force_replace → truncate (`>`), не append.
        assert "authorized_keys" in bash_cmd
        assert "grep -qxF" not in bash_cmd
        assert " > " in bash_cmd

    async def test_existing_user_with_key_runs_authorized_keys_step(self, monkeypatch):
        # getent found → usermod (no-op) → sudo -n true → authorized_keys
        conn = _conn([
            _run_result("deploy:x:1001:1001::/home/deploy:/bin/bash", "", 0),
            _sudo_probe(),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "ops", "p") as ssh:
            await ssh.create_user("deploy", public_key=_ED25519_PUB)
        # usermod не дёргается (нечего менять) — sразу authorized_keys.
        cmds = [c.args[0] for c in conn.run.await_args_list]
        assert any("authorized_keys" in c for c in cmds)

    async def test_invalid_key_prefix_rejected(self, monkeypatch):
        conn = _conn([
            _run_result("", "", 2),
            _sudo_probe(),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        with pytest.raises(SshError) as ei:
            async with SshClient("h", "ops", "p") as ssh:
                await ssh.create_user("deploy", public_key="not-a-key")
        assert ei.value.error_code == "SSH_INVALID_ARG"


class TestProvisionTaskWithInlineCreds:
    """`account.provision` тянет провижн-креды из Redis-stash'а."""

    async def test_inline_password_and_pubkey_used(
        self, make_task, fetch_task, captured_audit, monkeypatch,
        stash_dispatch_creds,
    ):
        # Provision-креды кладёт server_service в `dbos:dispatch_creds:<id>`
        # перед dispatch'ем; в payload едет только `creds_stash_key`.
        original_password = "GenStrongPwd!9X" * 2
        original_private_key = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n"
            "-----END OPENSSH PRIVATE KEY-----\n"
        )
        stash_key = await stash_dispatch_creds(
            password_plaintext=original_password,
            ssh_private_key_plaintext=original_private_key,
        )
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_inline",
            payload={
                "server_id": "srv_inline", "account_id": "acc_inline",
                "login": "ops",
                "ssh_public_key": _ED25519_PUB,
                "force_replace": True,
                "creds_stash_key": stash_key,
            },
        )
        fetch_calls: list = []

        async def fake_fetch(server_id, account_id, target_department_id=None):
            fetch_calls.append((server_id, account_id))
            return {"login": "ops", "password": "sess-pwd"}

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            fake_fetch,
        )
        # getent (not found) → sudo -n true → useradd → chpasswd → authorized_keys
        conn = _conn([
            _run_result("", "", 2),
            _sudo_probe(),
            _run_result("", "", 0),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status",
            fake_submit,
        )

        await users.account_provision.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # `_account_creds` всё равно тянет creds (для self-сессии) — но новый
        # пароль на chpasswd идёт из inline.
        chpasswd_stdin = conn.run.await_args_list[3].kwargs["input"]
        assert "ops:" + ("GenStrongPwd!9X" * 2) in chpasswd_stdin
        # Последняя команда — authorized_keys с truncate (force_replace=True).
        bash_cmd = conn.run.await_args_list[4].args[0]
        assert "authorized_keys" in bash_cmd
        assert " > " in bash_cmd  # truncate, не append

