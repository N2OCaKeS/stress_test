"""Гейт деструктивных операций: смена пароля / rotate / deprovision не
запускается, пока на сервере есть другая running-задача.

Покрывает:
  * деструктив откладывается (status → queued, attempt не растёт, audit
    «отложено»), пока на сервере крутится чужая running-задача;
  * деструктив выполняется, когда других running-задач нет;
  * не-деструктивная задача (inventory) гейтом НЕ затронута даже при чужой
    running-задаче на том же сервере;
  * гейт исключает саму себя (не зацикливается на собственном running).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh
import pytest

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo
from src.tasks import _runner, passwords, users
from tests._ssh_mock_helpers import run_result as _run_result


async def _insert_running(server_id: str, *, task_kind: str = "inventory.sync") -> str:
    """Завести уже-running задачу на сервере (имитация конкурента)."""
    import uuid

    tid = f"tsk_{uuid.uuid4().hex[:16]}"
    async with AsyncSessionLocal() as session:
        await task_repo.create(session, {
            "id": tid,
            "task_kind": task_kind,
            "target_server_id": server_id,
            "payload": {"server_id": server_id},
            "status": TaskStatus.RUNNING,
            "attempt": 1,
        })
        await session.commit()
    return tid


def _conn_ok():
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(return_value=_run_result("", "", 0))
    return conn


@pytest.fixture(autouse=True)
def _no_real_reschedule(monkeypatch):
    """Не дёргаем реальный re-kick в Redis — гоняем только DB-эффекты гейта."""
    async def noop(*a, **kw):
        return None
    monkeypatch.setattr(_runner, "_schedule_retry", noop)


class TestDestructiveDeferred:
    async def test_rotate_deferred_while_server_busy(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        await _insert_running("srv_busy")
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_busy",
            payload={"server_id": "srv_busy", "account_id": "acc_1"},
        )

        fetch = AsyncMock()
        submit = AsyncMock()
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", submit,
        )
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=_conn_ok()))

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        # Отложена обратно в queued, attempt откатан (mark_running поднимал до 1).
        assert t.status == TaskStatus.QUEUED
        assert t.attempt == 0
        assert t.scheduled_retry_at is not None
        # Ни fetch creds, ни submit — деструктив отбит ДО любого side-effect'а.
        fetch.assert_not_awaited()
        submit.assert_not_awaited()
        # Audit «отложено».
        reasons = [e.get("details", {}).get("reason") for e in captured_audit]
        assert "destructive_deferred_server_busy" in reasons

    async def test_deprovision_deferred_while_server_busy(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        await _insert_running("srv_busy2")
        tid = await make_task(
            task_kind="account.deprovision",
            target_server_id="srv_busy2",
            payload={
                "server_id": "srv_busy2", "account_id": "acc_1",
                "login": "ops", "is_managed": True,
            },
        )
        delete_user = AsyncMock()
        monkeypatch.setattr("src.tasks.users.ssh_client.delete_user", delete_user)
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_provision_status",
            AsyncMock(),
        )

        await users.account_deprovision.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.QUEUED
        delete_user.assert_not_awaited()


class TestDestructiveExecutesWhenClear:
    async def test_rotate_runs_when_no_other_running(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        # На сервере нет других running-задач — гейт пропускает.
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_free",
            payload={"server_id": "srv_free", "account_id": "acc_1"},
        )

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "OldPass!42", "host": "h"}

        submitted = []
        async def fake_submit(server_id, account_id, new_password, target_department_id=None):
            submitted.append(new_password)
            return {"rotated_at": "now"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=_conn_ok()))

        await passwords.account_rotate_password.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert len(submitted) == 1

    async def test_rotate_runs_after_competitor_finishes(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        """Конкурент сначала есть (defer), потом завершился — повтор проходит."""
        competitor = await _insert_running("srv_seq")
        tid = await make_task(
            task_kind="account.rotate_password",
            target_server_id="srv_seq",
            payload={"server_id": "srv_seq", "account_id": "acc_1"},
        )

        async def fake_fetch(server_id, account_id, target_department_id=None):
            return {"login": "ops", "password": "OldPass!42", "host": "h"}

        async def fake_submit(*a, **kw):
            return {"rotated_at": "now"}

        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.fetch_account_password", fake_fetch,
        )
        monkeypatch.setattr(
            "src.tasks.passwords.server_service_client.submit_rotated_password", fake_submit,
        )
        monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=_conn_ok()))

        # Первый заход — отложен.
        await passwords.account_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.QUEUED

        # Конкурент завершился.
        async with AsyncSessionLocal() as session:
            comp = await task_repo.get_by_id(session, competitor)
            await task_repo.mark_succeeded(session, comp, {"ok": True})
            await session.commit()

        # Повторный заход (re-kick подобрал бы ту же row) — теперь проходит.
        await passwords.account_rotate_password.original_func(tid)
        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED


class TestNonDestructiveUngated:
    async def test_inventory_not_gated_by_busy_server(
        self, make_task, fetch_task, captured_audit, monkeypatch,
    ):
        await _insert_running("srv_inv")
        tid = await make_task(
            task_kind="users.inventory",
            target_server_id="srv_inv",
            payload={"server_id": "srv_inv", "is_managed": True},
        )
        monkeypatch.setattr(
            "src.tasks.users.ssh_client.collect_os_users", AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            "src.tasks.users.ssh_client.os_users_facts_to_payload",
            MagicMock(return_value={"users": []}),
        )
        monkeypatch.setattr(
            "src.tasks.users.server_service_client.submit_users_inventory",
            AsyncMock(return_value={"diffs": []}),
        )

        await users.users_inventory.original_func(tid)

        t = await fetch_task(tid)
        # Не-деструктив прошёл несмотря на чужую running-задачу.
        assert t.status == TaskStatus.SUCCEEDED


class TestGateExcludesSelf:
    async def test_self_running_does_not_defer(self, make_task):
        """count_other_running_on_server не считает саму вызывающую задачу."""
        import uuid

        sid = "srv_self"
        tid = f"tsk_{uuid.uuid4().hex[:16]}"
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": tid,
                "task_kind": "account.rotate_password",
                "target_server_id": sid,
                "payload": {"server_id": sid},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
            })
            await session.commit()
        async with AsyncSessionLocal() as session:
            n = await task_repo.count_other_running_on_server(
                session, server_id=sid, exclude_task_id=tid,
            )
        assert n == 0
