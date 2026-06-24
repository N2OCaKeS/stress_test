"""Хендлер `management_user_sync` — недеструктивный re-bootstrap управляющей учётки.

* собирает management key-session-креды из payload и зовёт
  `ssh_client.sync_management_user` с пер-режимным конфигом;
* идемпотентен: повторный прогон той же задачи сходится к тому же результату,
  без деструктива;
* `rename_pending` из payload пробрасывается в результат, но сам rename
  хендлер не делает — синхронизирует под текущим управляющим пользователем.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import management_user


@pytest.fixture
def stub_sync(monkeypatch):
    """Заглушка `ssh_client.sync_management_user` — копит вызовы.

    SSH в этих тестах не мокаем на уровне asyncssh: проверяем именно контракт
    хендлера (payload → вызов sync_management_user → result). Сама
    `sync_management_user` и её идемпотентность покрыты SSH-тестами bootstrap'а.
    """
    calls: list[dict] = []

    async def fake_sync(credentials, server_id, *, management_user, public_key, modes=None):
        calls.append({
            "credentials": dict(credentials),
            "server_id": server_id,
            "management_user": management_user,
            "modes": modes,
        })
        return {
            "synced": True,
            "management_user": management_user,
            "management_mode": "astra_smolensk",
        }

    monkeypatch.setattr(
        "src.tasks.management_user.ssh_client.sync_management_user", fake_sync,
    )
    return calls


def _payload(**over) -> dict:
    base = {
        "server_id": "srv_sync",
        "host": "10.0.0.5",
        "ssh_port": 22,
        "is_managed": True,
        "management_user": "dbos",
        "management_login": "dbos",
        "management_modes": {
            "astra_smolensk": {"groups": ["astra-admin"],
                               "extra_create_commands": ["pdpl-user -i 63 dbos"]},
        },
    }
    base.update(over)
    return base


class TestSyncHandler:
    async def test_sync_calls_ssh_with_config(
        self, make_task, fetch_task, captured_audit, stub_sync,
    ):
        tid = await make_task(
            task_kind="management_user_sync",
            target_server_id="srv_sync",
            payload=_payload(),
        )
        await management_user.management_user_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["server_id"] == "srv_sync"
        assert t.result["synced"] is True
        assert t.result["management_user"] == "dbos"
        assert t.result["rename_pending"] is False

        assert len(stub_sync) == 1
        call = stub_sync[0]
        assert call["management_user"] == "dbos"
        assert call["credentials"]["is_managed"] is True
        assert call["credentials"]["host"] == "10.0.0.5"
        assert call["modes"]["astra_smolensk"]["groups"] == ["astra-admin"]

    async def test_sync_is_idempotent_on_rerun(
        self, make_task, fetch_task, captured_audit, stub_sync,
    ):
        from sqlalchemy import update

        from src.db.session import AsyncSessionLocal
        from src.models import Task

        tid = await make_task(
            task_kind="management_user_sync",
            target_server_id="srv_sync",
            payload=_payload(),
        )
        await management_user.management_user_sync.original_func(tid)
        first = await fetch_task(tid)
        assert first.status == TaskStatus.SUCCEEDED
        first_result = dict(first.result)
        assert len(stub_sync) == 1

        # Перезапуск task'а — re-dispatch/retry: `mark_running` CAS пропускает
        # только `queued`-row, поэтому возвращаем статус в queued (как сделал бы
        # повторный dispatch конфига или durable retry). Второй прогон должен
        # сойтись к тому же результату — re-bootstrap недеструктивен и
        # идемпотентен, повторный вызов sync_management_user ничего не ломает.
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Task)
                .where(Task.id == tid)
                .values(status=TaskStatus.QUEUED, attempt=0, max_attempts=3)
            )
            await session.commit()

        await management_user.management_user_sync.original_func(tid)
        second = await fetch_task(tid)
        assert second.status == TaskStatus.SUCCEEDED
        assert second.result == first_result
        assert len(stub_sync) == 2

    async def test_rename_pending_without_name_change_is_plain_sync(
        self, make_task, fetch_task, captured_audit, stub_sync, stub_cutover,
    ):
        # rename_pending=True, но имя в конфиге совпадает с текущим управляющим
        # пользователем — переименовывать нечего, идёт обычный sync, cutover не
        # вызывается.
        tid = await make_task(
            task_kind="management_user_sync",
            target_server_id="srv_sync",
            payload=_payload(
                management_user="dbos",
                management_login="dbos",
                rename_pending=True,
            ),
        )
        await management_user.management_user_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["rename_pending"] is True
        assert stub_sync[0]["management_user"] == "dbos"
        assert len(stub_cutover) == 0


@pytest.fixture
def stub_cutover(monkeypatch):
    """Заглушка `ssh_client.cutover_management_user` — копит вызовы.

    Возвращает успешный rename с удалённой старой учёткой. SSH-уровень
    (create под старым → verify нового → userdel старого) покрыт отдельно.
    """
    calls: list[dict] = []

    async def fake_cutover(
        credentials, server_id, *, old_management_user, new_management_user,
        public_key, modes=None, management_private_key_path=None,
    ):
        calls.append({
            "credentials": dict(credentials),
            "server_id": server_id,
            "old": old_management_user,
            "new": new_management_user,
            "key_path": management_private_key_path,
        })
        return {
            "renamed": True,
            "management_user": new_management_user,
            "management_mode": "astra_smolensk",
            "old_removed": True,
        }

    monkeypatch.setattr(
        "src.tasks.management_user.ssh_client.cutover_management_user", fake_cutover,
    )
    return calls


@pytest.fixture
def stub_submit_prepared(monkeypatch):
    """Заглушка `server_service_client.submit_prepared` — копит вызовы."""
    calls: list[dict] = []

    async def fake_submit(server_id, management_user, target_dept=None, *, management_mode=None):
        calls.append({
            "server_id": server_id,
            "management_user": management_user,
            "target_dept": target_dept,
            "management_mode": management_mode,
        })
        return {"ok": True}

    monkeypatch.setattr(
        "src.tasks.management_user.server_service_client.submit_prepared", fake_submit,
    )
    return calls


class TestCutoverRename:
    async def test_cutover_creates_verifies_removes_and_reports_new_user(
        self, make_task, fetch_task, captured_audit,
        stub_sync, stub_cutover, stub_submit_prepared,
    ):
        # login сменился в конфиге, rename_pending выставлен: cutover заводит
        # нового, проверяет, удаляет старого; result несёт новое имя; sync не
        # дёргается; server_service получает новое имя через submit_prepared.
        tid = await make_task(
            task_kind="management_user_sync",
            target_server_id="srv_sync",
            payload=_payload(
                management_user="dbos",
                management_login="newctl",
                rename_pending=True,
            ),
        )
        await management_user.management_user_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["management_user"] == "newctl"
        assert t.result["renamed"] is True
        assert t.result["old_removed"] is True

        assert len(stub_cutover) == 1
        assert stub_cutover[0]["old"] == "dbos"
        assert stub_cutover[0]["new"] == "newctl"
        # cutover заходит под старым управляющим пользователем.
        assert stub_cutover[0]["credentials"]["management_user"] == "dbos"
        # Обычный sync не вызывался — это rename-ветка.
        assert len(stub_sync) == 0
        # server_service узнал новое имя.
        assert len(stub_submit_prepared) == 1
        assert stub_submit_prepared[0]["management_user"] == "newctl"

    async def test_cutover_already_renamed_is_noop_sync(
        self, make_task, fetch_task, captured_audit,
        stub_sync, stub_cutover, stub_submit_prepared,
    ):
        # Повторный прогон после успешного rename: server.management_user уже
        # newctl, config.login=newctl — переименовывать нечего, идёт обычный
        # идемпотентный sync, cutover/submit_prepared не вызываются.
        tid = await make_task(
            task_kind="management_user_sync",
            target_server_id="srv_sync",
            payload=_payload(
                management_user="newctl",
                management_login="newctl",
                rename_pending=True,
            ),
        )
        await management_user.management_user_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.SUCCEEDED
        assert t.result["management_user"] == "newctl"
        assert len(stub_cutover) == 0
        assert len(stub_submit_prepared) == 0
        assert len(stub_sync) == 1
        assert stub_sync[0]["management_user"] == "newctl"

    async def test_cutover_verify_failure_keeps_old_and_fails(
        self, make_task, fetch_task, captured_audit,
        stub_sync, stub_submit_prepared, monkeypatch,
    ):
        # Анти-локаут: вход под новым не подтвердился → cutover поднимает ошибку,
        # старая учётка НЕ удалена (old_removed не достигается), server_service
        # о rename не уведомляется, task FAILED.
        from src.clients.ssh import SshError

        async def fail_cutover(*a, **kw):
            raise SshError(
                error_code="SSH_MANAGEMENT_KEY_VERIFY_FAILED",
                host="10.0.0.5",
                message="new login did not work",
            )

        monkeypatch.setattr(
            "src.tasks.management_user.ssh_client.cutover_management_user",
            fail_cutover,
        )
        tid = await make_task(
            task_kind="management_user_sync",
            target_server_id="srv_sync",
            payload=_payload(
                management_user="dbos",
                management_login="newctl",
                rename_pending=True,
            ),
        )
        await management_user.management_user_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status in (TaskStatus.FAILED, TaskStatus.QUEUED)
        assert t.status != TaskStatus.SUCCEEDED
        # server_service о новом имени не уведомлён — rename не завершён.
        assert len(stub_submit_prepared) == 0

    async def test_cutover_deferred_while_server_busy(
        self, make_task, fetch_task, captured_audit,
        stub_cutover, stub_submit_prepared, monkeypatch,
    ):
        # Гейт C1: пока на сервере есть другая running-задача, cutover (деструктив)
        # откладывается обратно в queued, cutover/submit_prepared не дёргаются.
        import uuid

        from src.db.session import AsyncSessionLocal
        from src.repositories import task as task_repo
        from src.tasks import _runner

        async def _noop_retry(*a, **kw):
            return None
        monkeypatch.setattr(_runner, "_schedule_retry", _noop_retry)

        other = f"tsk_{uuid.uuid4().hex[:16]}"
        async with AsyncSessionLocal() as session:
            await task_repo.create(session, {
                "id": other,
                "task_kind": "inventory.sync",
                "target_server_id": "srv_busy_cut",
                "payload": {"server_id": "srv_busy_cut"},
                "status": TaskStatus.RUNNING,
                "attempt": 1,
            })
            await session.commit()

        tid = await make_task(
            task_kind="management_user_sync",
            target_server_id="srv_busy_cut",
            payload=_payload(
                server_id="srv_busy_cut",
                management_user="dbos",
                management_login="newctl",
                rename_pending=True,
            ),
        )
        await management_user.management_user_sync.original_func(tid)

        t = await fetch_task(tid)
        assert t.status == TaskStatus.QUEUED
        assert len(stub_cutover) == 0
        assert len(stub_submit_prepared) == 0
        reasons = [e.get("details", {}).get("reason") for e in captured_audit]
        assert "destructive_deferred_server_busy" in reasons
