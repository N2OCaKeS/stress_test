"""Тесты бутстрапа управления сервером (#14).

* `SshClient.bootstrap_management_user` — useradd управляющего юзера + sudo,
  установка authorized_keys, идемпотентность (повтор не дублирует), отбой
  пустого/multiline-ключа;
* end-to-end handler `server.prepare`: read bootstrap-creds → scrub из payload →
  SSH bootstrap → submit_prepared callback; bootstrap-креды не в audit.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from src.clients.ssh import SshClient, SshError
from src.core.config import get_settings
from src.core.constants import TaskStatus
from src.tasks import prepare


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


_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc dbos"


# ── SshClient.bootstrap_management_user ──────────────────────────────────────


class TestBootstrapManagementUser:
    async def test_useradd_and_authorized_keys(self, monkeypatch):
        # getent (not found rc=2) → useradd (rc=0) → authorized_keys bash (rc=0)
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

        useradd_cmd = conn.run.await_args_list[1].args[0]
        assert "useradd" in useradd_cmd
        assert "dbos" in useradd_cmd
        assert "-G sudo" in useradd_cmd
        keys_cmd = conn.run.await_args_list[2].args[0]
        assert "authorized_keys" in keys_cmd
        assert "grep -qxF" in keys_cmd

    async def test_idempotent_existing_user(self, monkeypatch):
        # getent (found rc=0) → usermod (sync) → authorized_keys bash (rc=0)
        conn = _conn([
            _run_result("dbos:x:1100:1100::/home/dbos:/bin/bash", "", 0),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            # уже существующий пользователь — не падаем.
            await ssh.bootstrap_management_user("dbos", _PUBKEY)

    async def test_empty_public_key_rejected(self, monkeypatch):
        conn = _conn([])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user("dbos", "   ")
        assert ei.value.error_code == "SSH_INVALID_ARG"

    async def test_multiline_public_key_rejected(self, monkeypatch):
        conn = _conn([])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user("dbos", "key-a\nkey-b")
        assert ei.value.error_code == "SSH_INVALID_ARG"

    async def test_authorized_keys_failure_raises(self, monkeypatch):
        # getent (not found) → useradd (rc=0) → bash authorized_keys (rc=1)
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 0),
            _run_result("", "permission denied", 1),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
        async with SshClient("h", "boot", "boot-pwd") as ssh:
            with pytest.raises(SshError) as ei:
                await ssh.bootstrap_management_user("dbos", _PUBKEY)
        assert ei.value.error_code == "SSH_PREPARE_FAILED"


# ── Handler: server.prepare ──────────────────────────────────────────────────


class TestPrepareHandler:
    async def test_bootstrap_and_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        get_settings.cache_clear()
        monkeypatch.setenv("SSH_MANAGEMENT_USER", "dbos")
        monkeypatch.setenv("SSH_MANAGEMENT_PUBLIC_KEY", _PUBKEY)
        get_settings.cache_clear()

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep1",
            payload={
                "server_id": "srv_prep1",
                "bootstrap_login": "bootadmin",
                "bootstrap_password": "Boot1234",
                "host": "10.0.0.7",
                "target_department_id": "dep_a",
            },
        )

        # getent (not found) → useradd → authorized_keys bash
        conn = _conn([
            _run_result("", "", 2),
            _run_result("", "", 0),
            _run_result("", "", 0),
        ])
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(server_id, management_user, target_department_id=None):
            submit_calls.append((server_id, management_user, target_department_id))
            return {"ok": True, "is_managed": True, "prepared_at": "2026-05-27T00:00:00Z"}
        monkeypatch.setattr(
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["management_user"] == "dbos"
        assert t.result["prepared"] is True
        assert submit_calls == [("srv_prep1", "dbos", "dep_a")]

        get_settings.cache_clear()

    async def test_bootstrap_creds_scrubbed_from_payload(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        get_settings.cache_clear()
        monkeypatch.setenv("SSH_MANAGEMENT_USER", "dbos")
        monkeypatch.setenv("SSH_MANAGEMENT_PUBLIC_KEY", _PUBKEY)
        get_settings.cache_clear()

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep2",
            payload={
                "server_id": "srv_prep2",
                "bootstrap_login": "bootadmin",
                "bootstrap_password": "Boot1234",
            },
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
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)

        t = await fetch_task(tid)
        # Одноразовые креды стёрты из персистентной payload.
        assert "bootstrap_login" not in (t.payload or {})
        assert "bootstrap_password" not in (t.payload or {})
        # server_id остаётся.
        assert t.payload["server_id"] == "srv_prep2"

        get_settings.cache_clear()

    async def test_bootstrap_password_not_in_audit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        get_settings.cache_clear()
        monkeypatch.setenv("SSH_MANAGEMENT_USER", "dbos")
        monkeypatch.setenv("SSH_MANAGEMENT_PUBLIC_KEY", _PUBKEY)
        get_settings.cache_clear()

        tid = await make_task(
            task_kind="server.prepare", target_server_id="srv_prep3",
            payload={
                "server_id": "srv_prep3",
                "bootstrap_login": "bootadmin",
                "bootstrap_password": "Boot1234",
            },
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
            "src.tasks.prepare.server_service_client.submit_prepared", fake_submit,
        )

        await prepare.server_prepare.original_func(tid)
        assert "Boot1234" not in str(captured_audit)
        assert "bootadmin" not in str(captured_audit)

        get_settings.cache_clear()
