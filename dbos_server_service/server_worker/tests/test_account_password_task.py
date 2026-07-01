"""Integration-тесты `tasks/passwords.account_rotate_password` поверх реального
SshClient (mock'нутого через asyncssh.connect) и submit_rotated_password.

Покрывают полный round-trip:

  fetch_account_password → SshClient.set_password (chpasswd) →
  submit_rotated_password (отдать новый ciphertext server_service'у).

Фокус — что новый пароль НЕ утекает в audit и НЕ остаётся в task.result.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from src.core.constants import TaskStatus
from src.core.exceptions import CredentialFetchError
from src.tasks import passwords
from tests._ssh_mock_helpers import run_result as _run_result


def _conn_chpasswd_ok():
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()

    async def _run(cmd, **kw):
        # Пробер `sudo -n true` → rc!=0 (sudo требует пароль), чтобы sudo-пароль
        # подавался первой строкой stdin. Остальные команды — rc=0.
        rc = 1 if cmd == "sudo -n true" else 0
        return _run_result("", "", rc)

    conn.run = AsyncMock(side_effect=_run)
    return conn


def _conn_chpasswd_fails(stderr="permission denied", rc=1):
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(return_value=_run_result("", stderr, rc))
    return conn


class TestAccountRotateHappy:
    async def test_full_flow_fetch_ssh_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "OldPass!42", "host": "10.0.0.5"}

        conn = _conn_chpasswd_ok()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            submit_calls.append((server_id, account_id, new_password))
            return {"rotated_at": "2026-05-21T12:00:00Z"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["server_id"] == "srv_1"
        assert t.result["account_id"] == "acc_1"
        assert t.result["rotated_at"] == "2026-05-21T12:00:00Z"

        # SSH должен был получить chpasswd с новым паролем (длина 20)
        assert conn.run.await_count >= 1
        cmd = conn.run.await_args.args[0]
        assert "chpasswd" in cmd
        stdin = conn.run.await_args.kwargs["input"]
        # stdin format: "<sudo_pwd>\n<login>:<new_pwd>\n"
        assert "OldPass!42\n" in stdin  # sudo pwd
        assert "ops:" in stdin  # chpasswd payload, login part
        # submit вернул тот же новый пароль, что был в stdin
        assert len(submit_calls) == 1
        submitted_new = submit_calls[0][2]
        assert len(submitted_new) == 20
        assert f"ops:{submitted_new}\n" in stdin

    async def test_new_password_not_in_task_result(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )
        captured = {}

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "old", "host": "h"}

        conn = _conn_chpasswd_ok()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            captured["new"] = new_password
            return {"rotated_at": "now"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        # Новый пароль НЕ должен лежать в task.result (попадает в task-DB —
        # читаем audit-инвариант: result содержит rotated_at/server_id/account_id,
        # а сам plaintext — нет).
        assert captured["new"] not in str(t.result)

    async def test_new_password_not_in_audit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )
        captured_new = {}

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "old", "host": "h"}

        conn = _conn_chpasswd_ok()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            captured_new["pwd"] = new_password
            return {"rotated_at": "now"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        new_pwd = captured_new["pwd"]
        assert new_pwd not in str(captured_audit), "new password leaked into audit"


class TestAccountRotateSshFailure:
    async def test_chpasswd_failure_marks_task_failed_with_retry(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from src.tasks import _runner

        async def noop(*a, **kw):
            pass
        # Дефолт max_attempts=3 → mark_pending_for_retry, не FAILED.
        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "old", "host": "h"}

        conn = _conn_chpasswd_fails(stderr="permission denied", rc=1)
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        submit_calls = []
        async def fake_submit(*a, **kw):
            submit_calls.append(1)
            return {"rotated_at": "now"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        # На первом attempt'е с max=3 — переход в QUEUED для retry, не FAILED.
        assert t.status in (TaskStatus.QUEUED, TaskStatus.FAILED)
        # submit_rotated_password НЕ должен был быть вызван — chpasswd упал
        # ДО submit'а, иначе сохранили бы пароль, которым нельзя залогиниться.
        assert submit_calls == [], (
            "submit_rotated_password must NOT be called if chpasswd failed — "
            "otherwise server_service stores a password that does not work"
        )

    async def test_auth_failure_does_not_call_submit(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models import Task
        from src.tasks import _runner

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )
        async with AsyncSessionLocal() as session:
            await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
            await session.commit()

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "wrong", "host": "h"}

        monkeypatch.setattr(
            asyncssh, "connect",
            AsyncMock(side_effect=asyncssh.PermissionDenied(reason="bad")),
        )

        submit_calls = []
        async def fake_submit(*a, **kw):
            submit_calls.append(1)
            return {"rotated_at": "now"}
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert submit_calls == []


class TestAccountRotateSubmitFailure:
    async def test_submit_failure_marks_task_failed_after_ssh_changed_password(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Если SSH успешно сменил пароль, но submit обратно в server_service
        упал — task'а помечается failed: storage расходится с реальностью
        (пароль на хосте новый, в DB остался старый). Это «разъехались»
        состояние, оператор должен видеть failed-аудит и руками править."""
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models import Task
        from src.tasks import _runner

        async def noop(*a, **kw):
            pass
        monkeypatch.setattr(_runner, "_schedule_retry", noop)

        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )
        async with AsyncSessionLocal() as session:
            await session.execute(update(Task).where(Task.id == tid).values(max_attempts=1))
            await session.commit()

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "old", "host": "h"}

        conn = _conn_chpasswd_ok()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(*a, **kw):
            raise CredentialFetchError(
                error_code="PASSWORD_ROTATE_REJECTED",
                message="server_service returned 400",
            )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.FAILED
        assert "PASSWORD_ROTATE_REJECTED" in t.last_error
