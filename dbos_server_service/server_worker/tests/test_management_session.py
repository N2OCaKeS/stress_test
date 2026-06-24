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
from src.core.exceptions import CredentialFetchError
from src.services import ssh_client
from src.tasks import inventory, passwords, users
from tests._ssh_mock_helpers import run_result as _run_result


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


# ── set_account_password empty-password guard ──────────────────────────────────


class TestSetAccountPasswordEmptyGuard:
    """Ротация обязана выставить реальный секрет.

    Пустой/None пароль до `set_account_password` — это баг вызывающей стороны
    (потерянный stash, пустой fetch), не passwordless-провижн. Отбиваем явно
    `SSH_EMPTY_PASSWORD`, не открывая сессию, чтобы chpasswd не упал с
    `missing new password`, а set_password не сделал тихий no-op.
    """

    async def test_empty_password_rejected_without_session(self, monkeypatch):
        connect_mock = AsyncMock()
        monkeypatch.setattr(asyncssh, "connect", connect_mock)
        creds = {"login": "tester", "password": "x", "host": "10.0.0.5"}
        with pytest.raises(SshError) as ei:
            await ssh_client.set_account_password(creds, "srv_1", "tester", "")
        assert ei.value.error_code == "SSH_EMPTY_PASSWORD"
        connect_mock.assert_not_called()

    async def test_none_password_rejected_without_session(self, monkeypatch):
        connect_mock = AsyncMock()
        monkeypatch.setattr(asyncssh, "connect", connect_mock)
        creds = {"login": "tester", "password": "x", "host": "10.0.0.5"}
        with pytest.raises(SshError) as ei:
            await ssh_client.set_account_password(creds, "srv_1", "tester", None)  # type: ignore[arg-type]
        assert ei.value.error_code == "SSH_EMPTY_PASSWORD"
        connect_mock.assert_not_called()


# ── Handlers route through the management session ──────────────────────────────


def _fetch_creds(login="ops"):
    async def _f(server_id, account_id, target_department_id=None):
        return {"login": login, "password": "sess-pwd", "host": "10.0.0.5"}
    return _f


def _fetch_spy(login="ops", *, has_password=True):
    """fetch_account_password-замена со счётчиком вызовов.

    `has_password=False` имитирует discovered-аккаунт — server_service отдаёт
    404 `ACCOUNT_HAS_NO_PASSWORD`, фасад поднимает CredentialFetchError.
    """
    calls: list[tuple] = []

    async def _f(server_id, account_id, target_department_id=None):
        calls.append((server_id, account_id, target_department_id))
        if not has_password:
            raise CredentialFetchError(
                error_code="ACCOUNT_PASSWORD_UNAVAILABLE",
                message="server_service returned 404",
            )
        return {"login": login, "password": "sess-pwd", "host": "10.0.0.5"}

    _f.calls = calls
    return _f


class TestProvisionUsesManagementSession:
    async def test_managed_provision_connects_as_management_user(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
        stash_dispatch_creds,
    ):
        stash_key = await stash_dispatch_creds(password_plaintext="sess-pwd")
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_m1",
            payload={
                "server_id": "srv_m1", "account_id": "acc_1", "login": "ops",
                "has_sudo": True, "unix_groups": ["devs"], "shell": "/bin/bash",
                "is_managed": True, "management_user": "dbos",
                "creds_stash_key": stash_key,
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
        stash_dispatch_creds,
    ):
        stash_key = await stash_dispatch_creds(password_plaintext="sess-pwd")
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_m2",
            payload={
                "server_id": "srv_m2", "account_id": "acc_2", "login": "ops",
                "is_managed": False,
                "creds_stash_key": stash_key,
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
        stash_dispatch_creds,
    ):
        from sqlalchemy import update

        from src.db.session import AsyncSessionLocal
        from src.models import Task
        from src.tasks import _runner

        get_settings.cache_clear()
        monkeypatch.setenv("SSH_MANAGEMENT_PRIVATE_KEY_PATH", "")
        get_settings.cache_clear()
        stash_key = await stash_dispatch_creds(password_plaintext="sess-pwd")
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_m4",
            payload={
                "server_id": "srv_m4", "account_id": "acc_4", "login": "ops",
                "is_managed": True, "management_user": "dbos",
                "creds_stash_key": stash_key,
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


class TestCutoverManagementUser:
    """Cutover-rename управляющей учётки на SSH-уровне сервиса.

    Под старым пользователем заводим нового (`bootstrap_management_user` поверх
    ключевой сессии) → независимая key-сессия под новым проверяет вход + sudo →
    с новой сессии `userdel -r` старого. Все три захода идут под key-auth.
    """

    async def test_cutover_creates_verifies_then_removes_old(self, mgmt_key, monkeypatch):
        # Список соединений по порядку:
        #   1) create под старым: detect_mode → bootstrap (user_exists,
        #      useradd-flow, sudoers, authorized_keys). Кормим «mode probe» +
        #      «user не существует» + череду 0; bootstrap идемпотентен.
        #   2) verify под новым: один `sudo true`.
        #   3) delete под новым: getent (exists) → userdel → rm sudoers.
        from unittest.mock import AsyncMock

        from src.services import ssh_client

        # Достаточно «всё ок» (rc=0) на любой команде, кроме user_exists в
        # create-ветке, где rc=2 заставит bootstrap идти по useradd-пути.
        create_conn = _conn([
            _run_result("ASTRA=1\nLEVEL=2\n", "", 0),  # detect_management_mode
            _run_result("", "", 2),                     # bootstrap user_exists(new) → нет
            _run_result("", "", 2),                     # create_user user_exists(new) → нет
            _run_result("", "", 0),                     # useradd
            _run_result("", "", 0),                     # sudoers tee
            _run_result("", "", 0),                     # authorized_keys
        ])
        verify_conn = _conn([_run_result("", "", 0)])     # sudo true
        delete_conn = _conn([
            _run_result("oldctl:x:1001:1001::/home/oldctl:/bin/bash", "", 0),  # user_exists(old)
            _run_result("", "", 0),                     # userdel -r
            _run_result("", "", 0),                     # rm sudoers
        ])
        conns = [create_conn, verify_conn, delete_conn]
        connect_mock = AsyncMock(side_effect=conns)
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        creds = {"host": "10.0.0.5", "ssh_port": 22}
        result = await ssh_client.cutover_management_user(
            creds, "srv_cut",
            old_management_user="oldctl",
            new_management_user="newctl",
            public_key="ssh-ed25519 AAAAkey",
            modes={"astra_smolensk": {"groups": [], "extra_create_commands": []}},
            management_private_key_path=mgmt_key,
        )

        assert result["renamed"] is True
        assert result["management_user"] == "newctl"
        assert result["old_removed"] is True

        # 1) create под старым (по ключу), 2) verify под новым (по ключу),
        # 3) delete под новым (по ключу).
        calls = connect_mock.await_args_list
        assert len(calls) == 3
        assert calls[0].kwargs["username"] == "oldctl"
        assert calls[1].kwargs["username"] == "newctl"
        assert calls[1].kwargs["client_keys"] == [mgmt_key]
        assert calls[2].kwargs["username"] == "newctl"
        # Старого реально удаляем.
        userdel_cmd = delete_conn.run.await_args_list[1].args[0]
        assert "userdel" in userdel_cmd and "oldctl" in userdel_cmd

    async def test_cutover_verify_failure_keeps_old_user(self, mgmt_key, monkeypatch):
        from unittest.mock import AsyncMock

        from src.clients.ssh import SshError
        from src.services import ssh_client

        create_conn = _conn([
            _run_result("ASTRA=1\nLEVEL=2\n", "", 0),
            _run_result("", "", 2),
            _run_result("", "", 2),
            _run_result("", "", 0),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        # verify-сессия: sudo true вернул non-zero → анти-локаут срабатывает.
        verify_conn = _conn([_run_result("", "denied", 1)])
        connect_mock = AsyncMock(side_effect=[create_conn, verify_conn])
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        creds = {"host": "10.0.0.5", "ssh_port": 22}
        with pytest.raises(SshError) as ei:
            await ssh_client.cutover_management_user(
                creds, "srv_cut2",
                old_management_user="oldctl",
                new_management_user="newctl",
                public_key="ssh-ed25519 AAAAkey",
                modes={},
                management_private_key_path=mgmt_key,
            )
        assert ei.value.error_code == "SSH_MANAGEMENT_KEY_VERIFY_FAILED"
        # Только две сессии открыты — delete-сессия (третья) не создавалась,
        # старого не трогали.
        assert connect_mock.await_count == 2

    async def test_cutover_without_key_fails_before_touching_old(self, monkeypatch):
        # Управляющий ключ воркеру не настроен → `_build_session` поднимает
        # SSH_MANAGEMENT_KEY_MISSING на сборке старой сессии (как у любой другой
        # management-операции). Cutover падает ДО любого деструктива — ни одной
        # SSH-сессии не открыто, старый юзер не тронут.
        from unittest.mock import AsyncMock

        from src.clients.ssh import SshError
        from src.services import ssh_client

        get_settings.cache_clear()
        monkeypatch.setenv("SSH_MANAGEMENT_PRIVATE_KEY_PATH", "")
        get_settings.cache_clear()

        connect_mock = AsyncMock(side_effect=AssertionError("must not connect"))
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        creds = {
            "host": "10.0.0.5", "ssh_port": 22,
            "is_managed": True, "management_user": "oldctl",
        }
        with pytest.raises(SshError) as ei:
            await ssh_client.cutover_management_user(
                creds, "srv_cut3",
                old_management_user="oldctl",
                new_management_user="newctl",
                public_key="ssh-ed25519 AAAAkey",
                modes={},
                management_private_key_path=None,
            )
        assert ei.value.error_code == "SSH_MANAGEMENT_KEY_MISSING"
        connect_mock.assert_not_called()
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


def _inventory_conn():
    """SSHClientConnection-like mock c canned-результатами inventory-команд."""
    return _conn([
        _run_result("srv-01\n"),
        _run_result("Linux srv-01 5.15.0-91-generic\n"),
        _run_result('{"lscpu":[{"field":"Architecture:","data":"x86_64"}]}'),
        _run_result('{"blockdevices":[{"name":"sda","size":"500G","type":"disk"}]}'),
        _run_result('NAME="Astra Linux"\nVERSION_ID="1.7"\n'),
        _run_result('00:00.0 "Host bridge" "Intel"\n'),
    ])


class TestInventorySyncUsesManagementSession:
    async def test_managed_inventory_connects_as_management_user_no_fetch(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_inv_m",
            payload={
                "server_id": "srv_inv_m", "account_id": "acc_inv",
                "is_managed": True, "management_user": "dbos",
            },
        )
        spy = _fetch_spy("ops")
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.fetch_account_password", spy,
        )
        connect_mock = AsyncMock(return_value=_inventory_conn())
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts", fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # На управляемом сервере fetch ради auth не дёргается.
        assert spy.calls == []
        connect_kwargs = connect_mock.await_args.kwargs
        assert connect_kwargs["username"] == "dbos"
        assert connect_kwargs["password"] is None
        assert connect_kwargs["client_keys"] == [mgmt_key]

    async def test_unmanaged_inventory_keeps_self_session(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="inventory.sync", target_server_id="srv_inv_u",
            payload={"server_id": "srv_inv_u", "account_id": "acc_inv"},
        )
        spy = _fetch_spy("ops")
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.fetch_account_password", spy,
        )
        connect_mock = AsyncMock(return_value=_inventory_conn())
        monkeypatch.setattr(asyncssh, "connect", connect_mock)

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.inventory.server_service_client.submit_inventory_facts", fake_submit,
        )

        await inventory.inventory_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # На неуправляемом сервере self-сессия требует login+password из fetch.
        assert len(spy.calls) == 1
        connect_kwargs = connect_mock.await_args.kwargs
        assert connect_kwargs["username"] == "ops"
        assert connect_kwargs["password"] == "sess-pwd"
        assert connect_kwargs["client_keys"] is None


class TestManagedSkipsFetchForAuth:
    async def test_managed_deprovision_does_not_fetch(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="account.deprovision", target_server_id="srv_d_nf",
            payload={
                "server_id": "srv_d_nf", "account_id": "acc_d", "login": "ops",
                "is_managed": True, "management_user": "dbos",
            },
        )
        spy = _fetch_spy("ops")
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password", spy,
        )
        conn = _conn([
            _run_result("ops:x:1001:1001::/home/ops:/bin/bash", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_deprovision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert spy.calls == []

    async def test_managed_update_does_not_fetch(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="account.update_on_host", target_server_id="srv_u_nf",
            payload={
                "server_id": "srv_u_nf", "account_id": "acc_u", "login": "ops",
                "has_sudo": True, "unix_groups": ["devs"], "shell": "/bin/bash",
                "is_managed": True, "management_user": "dbos",
            },
        )
        spy = _fetch_spy("ops")
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password", spy,
        )
        # usermod-путь: user_exists (getent) → usermod groups → usermod shell.
        conn = _conn([
            _run_result("ops:x:1001:1001::/home/ops:/bin/bash", "", 0),
            _run_result("", "", 0),
            _run_result("", "", 0),
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
        assert spy.calls == []

    async def test_managed_rotate_with_payload_login_does_not_fetch(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="account.rotate_password", target_server_id="srv_r_nf",
            payload={
                "server_id": "srv_r_nf", "account_id": "acc_r", "login": "appuser",
                "is_managed": True, "management_user": "dbos",
            },
        )
        spy = _fetch_spy("appuser")
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", spy,
        )
        conn = _conn([_run_result("", "", 0)])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            return {"rotated_at": "2026-05-27T00:00:00Z"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # login есть в payload → fetch не нужен, discovered-аккаунт ротируется.
        assert spy.calls == []


class TestManagedDiscoveredAccountProvision:
    # discovered+no_password теперь 409 fail-fast в server_service ещё до
    # `dispatch_outbox` — worker эту таску в принципе не получит. Тест
    # сценария «worker получил discovered+no_password» удалён как
    # неработоспособный после смены контракта.
    @pytest.mark.skip(
        reason="discovered+no_password — 409 fail-fast в server, не доходит до worker"
    )
    async def test_managed_provision_no_password_skips_chpasswd(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
    ):
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_disc",
            payload={
                "server_id": "srv_disc", "account_id": "acc_disc", "login": "ops",
                "is_managed": True, "management_user": "dbos",
            },
        )
        # discovered-аккаунт: пароля нет → fetch отдаёт 404.
        spy = _fetch_spy("ops", has_password=False)
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password", spy,
        )
        # getent (not found) → useradd. chpasswd НЕ должен вызываться.
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            return {"ok": True}
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status", fake_submit,
        )

        await users.account_provision.original_func(tid)

        t = await fetch_task(tid)
        # Задача НЕ падает, хотя пароля нет — шаг chpasswd пропущен.
        assert t.status == TaskStatus.SUCCEEDED
        # Best-effort fetch ради пароля сделан один раз и вернул 404.
        assert len(spy.calls) == 1
        # Только две команды: getent + useradd, без chpasswd.
        assert conn.run.await_count == 2

    async def test_managed_provision_with_password_sets_it(
        self, make_task, fetch_task, captured_audit, monkeypatch, mgmt_key,
        stash_dispatch_creds,
    ):
        # Managed-provision: password приходит из dispatch-stash'а,
        # `_fetch_password_to_set` к server_service не зовётся — креды уже
        # есть.
        stash_key = await stash_dispatch_creds(password_plaintext="sess-pwd")
        tid = await make_task(
            task_kind="account.provision", target_server_id="srv_pw",
            payload={
                "server_id": "srv_pw", "account_id": "acc_pw", "login": "ops",
                "is_managed": True, "management_user": "dbos",
                "creds_stash_key": stash_key,
            },
        )
        spy = _fetch_spy("ops", has_password=True)
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password", spy,
        )
        # getent (not found) → useradd → chpasswd.
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

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # Креды пришли через stash, fetch_account_password не дёргался.
        assert spy.calls == []
        assert conn.run.await_count == 3
