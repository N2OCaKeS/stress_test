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

    async def test_rename_pending_propagated_without_rename(
        self, make_task, fetch_task, captured_audit, stub_sync,
    ):
        # login сменился в конфиге (management_login != management_user сервера),
        # rename_pending выставлен. Хендлер синкает под ТЕКУЩИМ управляющим
        # пользователем сервера (dbos), а не под новым — rename не делает.
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
        assert t.result["rename_pending"] is True
        # sync идёт под текущим управляющим пользователем сервера, не под new.
        assert stub_sync[0]["management_user"] == "dbos"
