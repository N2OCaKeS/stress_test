"""Cred резолвится в момент СТАРТА задачи, не постановки.

Инвариант C1.1: если пароль учётки сменился между enqueue и фактическим
исполнением, воркер должен использовать СВЕЖИЙ пароль. Креды для self-сессии
читаются из server_service на старте `_impl` (`fetch_account_password`), а не
вшиваются в payload при диспатче. Тест имитирует смену пароля между enqueue и
стартом и проверяет, что в SSH-сессию (sudo-пароль chpasswd'а) ушёл свежий, а
в payload снимка пароля нет вовсе.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh

from src.core.constants import TaskStatus
from src.tasks import passwords
from tests._ssh_mock_helpers import run_result as _run_result


def _conn_ok():
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(return_value=_run_result("", "", 0))
    return conn


class TestCredResolvedAtStart:
    async def test_fresh_password_used_when_changed_after_enqueue(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        # Payload НЕ несёт пароля — только идентификаторы. Это и есть гарантия,
        # что снимок на enqueue невозможен: пароль негде взять, кроме fetch'а.
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )

        # На enqueue пароль был STALE; к старту исполнения server_service отдаёт
        # уже FRESH (его сменили в промежутке). fetch вызывается в impl, на
        # старте — значит вернёт свежий.
        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "FreshSudo!99", "host": "h"}

        conn = _conn_ok()
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            return {"rotated_at": "now"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        # chpasswd шлёт sudo-пароль (self/managed sudo) в stdin — это и есть
        # пароль, прочитанный на старте. Должен быть FRESH, не STALE.
        stdin = conn.run.await_args.kwargs["input"]
        assert "FreshSudo!99\n" in stdin
        assert "StaleSudo" not in stdin
        # Снимок пароля в payload не оседает.
        assert "FreshSudo!99" not in str(t.payload)

    async def test_payload_carries_no_password_snapshot(self, make_task, fetch_task):
        """Dispatch-payload rotate-задачи не содержит plaintext пароля."""
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_1",
            payload={"server_id": "srv_1", "account_id": "acc_1"},
        )
        t = await fetch_task(tid)
        assert "password" not in t.payload
        assert "password_plaintext" not in t.payload
