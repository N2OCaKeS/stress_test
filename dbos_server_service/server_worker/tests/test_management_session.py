"""Выбор SSH-сессии под привилегированные операции на подготовленном сервере.

После `server.prepare` сервер помечается управляемым, и worker для
provision/update/deprovision/rotate/users-inventory должен заходить под
управляющим пользователем по ключу с sudo, а не self-сессией под аккаунтом.
Не управляемый сервер сохраняет старое поведение (login + password).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from src.clients.ssh import SshError
from src.core.config import get_settings
from src.core.constants import TaskStatus
from src.services import ssh_client
from src.tasks import passwords, users


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


@pytest.fixture
def mgmt_key(tmp_path, monkeypatch):
    """Положить фиктивный приватный ключ и указать на него в конфиге."""
    key_path = tmp_path / "mgmt_id_ed25519"
    key_path.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n")
    get_settings.cache_clear()
    monkeypatch.setenv("SSH_MANAGEMENT_USER", "dbos")
    monkeypatch.setenv("SSH_MANAGEMENT_PRIVATE_KEY_PATH", str(key_path))
    get_settings.cache_clear()
    yield str(key_path)
    get_settings.cache_clear()


# ── _build_session ────────────────────────────────────────────────────────────


class TestBuildSession:
    def test_unmanaged_uses_account_self_session(self):
        creds = {"login": "ops", "password": "sess-pwd", "host": "10.0.0.5"}
        ssh = ssh_client._build_session(creds, "srv_1")
        assert ssh.username == "ops"
        assert ssh._password == "sess-pwd"
        assert ssh._client_keys is None

    def test_managed_uses_management_key_session(self, mgmt_key):
        creds = {
            "login": "ops", "password": "sess-pwd", "host": "10.0.0.5",
            "is_managed": True, "management_user": "dbos",
        }
        ssh = ssh_client._build_session(creds, "srv_1")
        assert ssh.username == "dbos"
        # На ключевой сессии пароль аккаунта в auth не идёт.
        assert ssh._password is None
        assert ssh._client_keys == [mgmt_key]

    def test_managed_falls_back_to_config_user(self, mgmt_key):
        # management_user в payload нет — берём дефолт из конфига.
        creds = {"login": "ops", "password": "x", "host": "h", "is_managed": True}
        ssh = ssh_client._build_session(creds, "srv_1")
        assert ssh.username == "dbos"

    def test_managed_without_key_raises_clear_error(self, monkeypatch):
        get_settings.cache_clear()
        monkeypatch.setenv("SSH_MANAGEMENT_PRIVATE_KEY_PATH", "")
        get_settings.cache_clear()
        creds = {"login": "ops", "password": "x", "host": "h", "is_managed": True}
        with pytest.raises(SshError) as ei:
            ssh_client._build_session(creds, "srv_1")
        assert ei.value.error_code == "SSH_MANAGEMENT_KEY_MISSING"
        get_settings.cache_clear()

    def test_managed_with_missing_key_file_raises(self, monkeypatch):
        get_settings.cache_clear()
        monkeypatch.setenv("SSH_MANAGEMENT_PRIVATE_KEY_PATH", "/no/such/key")
        get_settings.cache_clear()
        creds = {"login": "ops", "password": "x", "host": "h", "is_managed": True}
        with pytest.raises(SshError) as ei:
            ssh_client._build_session(creds, "srv_1")
        assert ei.value.error_code == "SSH_MANAGEMENT_KEY_MISSING"
        get_settings.cache_clear()


# ── Handlers route through the management session ──────────────────────────────


def _fetch_creds(login="ops"):
    async def _f(server_id, account_id, target_department_id=None):
        return {"login": login, "password": "sess-pwd", "host": "10.0.0.5"}
    return _f


class TestProvisionUsesManagementSession:
    async def test_managed_provision_connects_as_management_user(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_m1",
            payload={
                "server_id": "srv_m1", "account_id": "acc_1", "login": "ops",
                "has_sudo": True, "unix_groups": ["devs"], "shell": "/bin/bash",
                "is_managed": True, "management_user": "dbos",
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
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)
        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_provision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # Сессия открыта под управляющим пользователем по ключу.
        connect_kwargs = connect_mock.await_args.kwargs
        assert connect_kwargs["username"] == "dbos"
        assert connect_kwargs["password"] is None
        assert connect_kwargs["client_keys"] == [mgmt_key]
        # Заводим всё равно сам аккаунт.
        useradd_cmd = conn.run.await_args_list[1].args[0]
        assert "useradd" in useradd_cmd and useradd_cmd.rstrip().endswith("ops")

    async def test_unmanaged_provision_keeps_self_session(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_m2",
            payload={
                "server_id": "srv_m2", "account_id": "acc_2", "login": "ops",
                "is_managed": False,
            },
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
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)
        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_provision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        connect_kwargs = connect_mock.await_args.kwargs
        assert connect_kwargs["username"] == "ops"
        assert connect_kwargs["password"] == "sess-pwd"
        assert connect_kwargs["client_keys"] is None

    async def test_managed_deprovision_connects_as_management_user(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="account.deprovision", target_server_id="srv_m3",
            payload={
                "server_id": "srv_m3", "account_id": "acc_3", "login": "ops",
                "is_managed": True, "management_user": "dbos",
            },
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        conn = _conn([
            _run_result("ops:x:1001:1001::/home/ops:/bin/bash", "", 0),
            _run_result("", "", 0),
        ])
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)
        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_deprovision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert connect_mock.await_args.kwargs["username"] == "dbos"

    async def test_managed_without_key_fails_task_clearly(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from sqlalchemy import update

        from src.db.session import AsyncSessionLocal
        from src.models import Task
        from src.tasks import _runner

        get_settings.cache_clear()
        monkeypatch.setenv("SSH_MANAGEMENT_PRIVATE_KEY_PATH", "")
        get_settings.cache_clear()
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_m4",
            payload={
                "server_id": "srv_m4", "account_id": "acc_4", "login": "ops",
                "is_managed": True, "management_user": "dbos",
            },
        )
        # max_attempts=1 → terminal FAILED после одной ошибки.
        async with AsyncSessionLocal() as session:
            await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
            await session.commit()

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            _fetch_creds("ops"),
        )
        # asyncssh.connect не должен вызываться — падаем на сборке сессии.
        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=AssertionError("must not connect")),
        )

        await users.account_provision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert t.last_error and "SSH_MANAGEMENT_KEY_MISSING" in t.last_error
        get_settings.cache_clear()


class TestRotateUsesManagementSession:
    async def test_managed_rotate_connects_as_management_user(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="account.rotate_password", target_server_id="srv_r1",
            payload={
                "server_id": "srv_r1", "account_id": "acc_r1",
                "is_managed": True, "management_user": "dbos",
            },
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password",
            _fetch_creds("appuser"),
        )
        # chpasswd через sudo: одна run-команда.
        conn = _conn([_run_result("", "", 0)])
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)
        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            return {"rotated_at": "2026-05-27T00:00:00Z"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert connect_mock.await_args.kwargs["username"] == "dbos"
        assert connect_mock.await_args.kwargs["client_keys"] == [mgmt_key]


class TestUsersInventoryUsesManagementSession:
    async def test_managed_inventory_connects_as_management_user(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="users.inventory", target_server_id="srv_i1",
            payload={
                "server_id": "srv_i1", "is_managed": True,
                "management_user": "dbos",
            },
        )
        # getent passwd / group / login.defs — три run-команды.
        conn = _conn([
            _run_result("root:x:0:0::/root:/bin/bash", "", 0),
            _run_result("sudo:x:27:dbos", "", 0),
            _run_result("UID_MIN 1000", "", 0),
        ])
        connect_mock = AsyncMock(return_value=conn)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)
        async def fake_submit(server_id, users_payload, target_department_id=None):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_users_inventory", fake_submit,
        )

        await users.users_inventory.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert connect_mock.await_args.kwargs["username"] == "dbos"
        assert connect_mock.await_args.kwargs["client_keys"] == [mgmt_key]
