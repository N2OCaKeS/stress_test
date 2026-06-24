"""Фан-аут `management_user_sync` при PUT `/management-user-config`.

Проверяем:
* изменение `modes` ставит high-priority sync на ВСЕ подготовленные
  (`is_managed`) серверы и не трогает неподготовленные;
* задачи уходят с `priority=TASK_PRIORITY_HIGH`;
* смена `login` помечается `rename_pending` без выполнения rename
  (rename-часть в payload не уходит — только флаг);
* PUT без реального изменения modes (тот же конфиг) фан-аут не запускает.
"""

from __future__ import annotations

import pytest

from src.services import worker_client
from tests._helpers import auth_hdr as _hdr

BASE = "/api/server/v1/management-user-config"


@pytest.fixture
def captured_sync(monkeypatch):
    """Перехват `dispatch_task_with_hit` в management_user_config-сервисе.

    Фан-аут зовёт `worker_client.dispatch_task_with_hit` через alias
    `src.services.management_user_config.worker_client`. Патчим именно его —
    cross-DB INSERT в тестах не делаем.
    """
    calls: list[dict] = []

    async def fake_dispatch_with_hit(
        *, db, task_kind, target_server_id, payload, created_by,
        request_id, target_resource_id=None, idempotency_key=None, priority=0,
    ):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "priority": priority,
        })
        return f"tsk_{len(calls)}", False

    monkeypatch.setattr(
        "src.services.management_user_config.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


async def _make_managed(make_server, db, *, dept="dep_a"):
    srv = await make_server(department_id=dept)
    srv.is_managed = True
    srv.management_user = "dbos"
    await db.flush()
    return srv


class TestModesChangeFanout:
    async def test_modes_change_fans_out_to_managed_only(
        self, client, account_admin_token, make_server, db, captured_sync,
    ):
        managed1 = await _make_managed(make_server, db)
        managed2 = await _make_managed(make_server, db, dept="dep_b")
        # Неподготовленный сервер — фан-аут его не трогает.
        await make_server(department_id="dep_a")

        resp = await client.put(
            BASE, headers=_hdr(account_admin_token),
            json={"modes": {"astra_smolensk": {"groups": ["astra-admin"]}}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["modes_changed"] is True

        targets = {c["target_server_id"] for c in captured_sync}
        assert targets == {managed1.id, managed2.id}
        assert all(c["task_kind"] == "management_user_sync" for c in captured_sync)

        fanout = body["sync_fanout"]
        assert fanout is not None
        assert {d["server_id"] for d in fanout["dispatched"]} == {
            managed1.id, managed2.id
        }
        assert fanout["truncated"] == 0

    async def test_sync_dispatched_with_high_priority(
        self, client, account_admin_token, make_server, db, captured_sync,
    ):
        await _make_managed(make_server, db)

        await client.put(
            BASE, headers=_hdr(account_admin_token),
            json={"modes": {"other_os": {"groups": ["sudo"]}}},
        )
        assert captured_sync
        for call in captured_sync:
            assert call["priority"] == worker_client.TASK_PRIORITY_HIGH

    async def test_payload_carries_current_config(
        self, client, account_admin_token, make_server, db, captured_sync,
    ):
        await _make_managed(make_server, db)

        await client.put(
            BASE, headers=_hdr(account_admin_token),
            json={
                "login": "dbos",  # тот же login → не rename
                "modes": {"astra_orel": {"groups": ["orel-g"],
                                         "extra_create_commands": ["echo hi"]}},
            },
        )
        payload = captured_sync[0]["payload"]
        assert payload["management_login"] == "dbos"
        assert payload["management_modes"]["astra_orel"]["groups"] == ["orel-g"]
        assert payload["management_modes"]["astra_orel"]["extra_create_commands"] == [
            "echo hi"
        ]
        assert payload["rename_pending"] is False

    async def test_unchanged_modes_no_fanout(
        self, client, account_admin_token, make_server, db, captured_sync,
    ):
        await _make_managed(make_server, db)
        # Первый PUT ставит конфиг (фан-аут #1).
        await client.put(
            BASE, headers=_hdr(account_admin_token),
            json={"modes": {"other_os": {"groups": ["sudo"]}}},
        )
        first_count = len(captured_sync)
        assert first_count >= 1
        captured_sync.clear()
        # Повторный PUT с теми же значениями — modes_changed=False, фан-аута нет.
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token),
            json={"modes": {"other_os": {"groups": ["sudo"]}}},
        )
        body = resp.json()
        assert body["modes_changed"] is False
        assert body["sync_fanout"] is None
        assert captured_sync == []


class TestLoginRenamePending:
    async def test_login_change_marks_rename_pending_without_rename(
        self, client, account_admin_token, make_server, db, captured_sync,
    ):
        await _make_managed(make_server, db)

        resp = await client.put(
            BASE, headers=_hdr(account_admin_token),
            json={"login": "newctl"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["login_changed"] is True
        assert body["rename_pending"] is True
        assert body["previous_login"] == "dbos"

        # Фан-аут синка всё равно уехал (под текущим management_user),
        # но rename-пометка стоит, а сам rename не выполняется: payload несёт
        # только флаг rename_pending, никакой rename/cutover-команды.
        fanout = body["sync_fanout"]
        assert fanout is not None
        assert fanout["rename_pending"] is True
        assert captured_sync
        for call in captured_sync:
            assert call["payload"]["rename_pending"] is True
            # Новый login едет как целевой в management_login, но handler сам
            # rename не делает — это контракт фазы C.
            assert call["payload"]["management_login"] == "newctl"

    async def test_no_managed_servers_empty_fanout(
        self, client, account_admin_token, captured_sync,
    ):
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token),
            json={"modes": {"other_os": {"groups": ["sudo"]}}},
        )
        body = resp.json()
        assert body["modes_changed"] is True
        assert body["sync_fanout"]["dispatched"] == []
        assert captured_sync == []
