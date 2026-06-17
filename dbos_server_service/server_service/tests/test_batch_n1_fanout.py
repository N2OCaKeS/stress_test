"""Регрессия на N+1 в массовых fan-out / mass-rotation / reconcile.

Зона: `worker_dispatch.py` (mass rotate + edit fan-out) и
`internal_service.receive_users_inventory` (reconcile).

Контракт: при работе с N связанными серверами / N login'ами идёт фиксированное
число SELECT'ов (1 батч), а не N round-trip'ов. Шпионим за репозиторными
функциями `repo.get_by_id`, `repo.get_many_by_ids`,
`account_repo.get_account_on_server_by_login`, `account_repo.get_link`
и считаем `call_count`.

Раньше:

* `account_rotate_password_dispatch` (mode=all) → N x `load_visible_server`
  (каждый = один SELECT по `servers.id`) для N привязанных серверов.
* `fanout_update_on_host` (PATCH OS-managed поля) → то же самое.
* `receive_users_inventory` → 2·N SELECT'ов на N юзерах
  (`get_account_on_server_by_login` + `get_link` в цикле).

Сейчас:

* dispatch'ы — один `repo.get_many_by_ids(WHERE id IN (...))`.
* reconcile — один `list_accounts_on_server_by_logins` +
  один `list_links_for_server_by_account_ids`.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"
BASE_INT = "/api/server/v1/internal"


from tests._helpers import auth_hdr as _hdr  # noqa: E402


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Заглушка `worker_client.dispatch_task` — таски не уходят в Redis."""
    calls: list[dict] = []

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


@pytest.fixture
def repo_call_spy(monkeypatch):
    """Счётчики обращений в server-репозиторий.

    Сохраняем оригинальные coroutine'ы и оборачиваем их, чтобы не сломать
    реальные SQL-вызовы (тестируем поведение под нагрузкой N серверов, а не
    отсутствие БД). Каждый враппер инкрементит свой счётчик и проксит вызов.
    """
    from src.repositories import server as server_repo

    counts = {"get_by_id": 0, "get_many_by_ids": 0}
    original_get_by_id = server_repo.get_by_id
    original_get_many = server_repo.get_many_by_ids

    async def spy_get_by_id(db, server_id):
        counts["get_by_id"] += 1
        return await original_get_by_id(db, server_id)

    async def spy_get_many(db, server_ids):
        counts["get_many_by_ids"] += 1
        return await original_get_many(db, server_ids)

    monkeypatch.setattr(server_repo, "get_by_id", spy_get_by_id)
    monkeypatch.setattr(server_repo, "get_many_by_ids", spy_get_many)

    return counts


@pytest.fixture
def reconcile_call_spy(monkeypatch):
    """Счётчики per-юзер запросов reconcile + batch-методов.

    `get_account_on_server_by_login` и `get_link` — это per-юзер SELECT'ы
    старого reconcile'а. После батча основной цикл их не зовёт вовсе — на любом
    payload'е должны быть нулями. Batch-методы инкрементятся ровно по одному
    за вызов.
    """
    from src.repositories import server_account as acc_repo

    counts = {
        "get_account_on_server_by_login": 0,
        "get_link": 0,
        "list_accounts_on_server_by_logins": 0,
        "list_links_for_server_by_account_ids": 0,
    }

    original_get_acc_by_login = acc_repo.get_account_on_server_by_login
    original_get_link = acc_repo.get_link
    original_list_accounts = acc_repo.list_accounts_on_server_by_logins
    original_list_links = acc_repo.list_links_for_server_by_account_ids

    async def spy_get_acc_by_login(db, server_id, login):
        counts["get_account_on_server_by_login"] += 1
        return await original_get_acc_by_login(db, server_id, login)

    async def spy_get_link(db, account_id, server_id):
        counts["get_link"] += 1
        return await original_get_link(db, account_id, server_id)

    async def spy_list_accounts(db, server_id, logins):
        counts["list_accounts_on_server_by_logins"] += 1
        return await original_list_accounts(db, server_id, logins)

    async def spy_list_links(db, server_id, account_ids):
        counts["list_links_for_server_by_account_ids"] += 1
        return await original_list_links(db, server_id, account_ids)

    monkeypatch.setattr(
        acc_repo, "get_account_on_server_by_login", spy_get_acc_by_login,
    )
    monkeypatch.setattr(acc_repo, "get_link", spy_get_link)
    monkeypatch.setattr(
        acc_repo, "list_accounts_on_server_by_logins", spy_list_accounts,
    )
    monkeypatch.setattr(
        acc_repo, "list_links_for_server_by_account_ids", spy_list_links,
    )
    return counts


# ── mass rotate: один SELECT WHERE id IN на N серверах ──────────────────────


class TestMassRotateBatchLoad:
    """`/server-accounts/{id}/rotate` без `server_id` — load одним батчем."""

    async def test_mass_rotate_uses_batch_select(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, repo_call_spy,
    ):
        # Шесть привязанных серверов — заметно больше «1», но достаточно
        # лёгкое для теста. Worst-case экономия: было бы 6 SELECT'ов, стало 1.
        servers = [
            await make_server(department_id="dep_a") for _ in range(6)
        ]
        acc = await make_account(
            server_ids=[s.id for s in servers], login="ops",
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch) == 6

        assert repo_call_spy["get_many_by_ids"] == 1, (
            "ожидался один батч-load, фактически "
            f"{repo_call_spy['get_many_by_ids']}"
        )
        assert repo_call_spy["get_by_id"] == 0, (
            "ни одного per-server SELECT по PK, фактически "
            f"{repo_call_spy['get_by_id']}"
        )

    async def test_targeted_rotate_also_uses_batch(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch, repo_call_spy,
    ):
        """Точечная (`?server_id=`) идёт по той же batch-ветке.

        Список из одного id всё равно проходит через `load_visible_servers` —
        контракт «один SELECT на dispatch» держится в обоих режимах.
        """
        servers = [
            await make_server(department_id="dep_a") for _ in range(3)
        ]
        acc = await make_account(
            server_ids=[s.id for s in servers], login="ops",
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
            params={"server_id": servers[1].id},
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch) == 1

        assert repo_call_spy["get_many_by_ids"] == 1
        assert repo_call_spy["get_by_id"] == 0


# ── edit fan-out: PATCH `has_sudo` → один SELECT WHERE id IN ────────────────


class TestEditFanoutBatchLoad:
    """PATCH OS-managed атрибута → `fanout_update_on_host` грузит серверы батчем."""

    async def test_managed_attr_fanout_uses_batch_select(
        self, client, admin_role_token_a, make_server, make_account,
        captured_dispatch, repo_call_spy,
    ):
        servers = [
            await make_server(department_id="dep_a") for _ in range(5)
        ]
        acc = await make_account(
            server_ids=[s.id for s in servers],
            login="shared",
            has_sudo=False,
        )

        # До PATCH'а монипатч счётчиков уже стоит; сам PATCH не должен
        # дёргать get_by_id per-server — только fan-out батчем.
        resp = await client.patch(
            f"{BASE}/server-accounts/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"has_sudo": True, "unix_groups": ["sudo"]},
        )
        assert resp.status_code == 200, resp.text
        # Fan-out ушёл на все 5 серверов.
        assert len(captured_dispatch) == 5
        assert {c["target_server_id"] for c in captured_dispatch} == {
            s.id for s in servers
        }

        # `fanout_update_on_host` грузит цели одним батчем.
        # На сам PATCH (без fan-out'а) обращений к `server.get_by_id` нет.
        assert repo_call_spy["get_many_by_ids"] == 1, (
            f"ожидался один батч-load в fan-out'е, "
            f"фактически {repo_call_spy['get_many_by_ids']}"
        )
        assert repo_call_spy["get_by_id"] == 0


# ── reconcile: один SELECT по login'ам, один по account_id'ам ───────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestUsersInventoryReconcileBatch:
    """`receive_users_inventory` — батчит lookup'ы аккаунтов и links."""

    async def test_reconcile_uses_batch_lookups(
        self, client, worker_bot_token_a, make_server, make_account,
        db, dept_a, reconcile_call_spy,
    ):
        # На сервере уже привязано 4 аккаунта (известных в БД), плюс worker
        # увидел двух «чужих» юзеров — итого 6 login'ов в payload'е. Раньше
        # это были 2·6 = 12 round-trip'ов; теперь — 2 (batch).
        srv = await make_server(department_id=dept_a)
        for login in ("ops_a", "ops_b", "ops_c", "ops_d"):
            await make_account(server_id=srv.id, login=login, has_sudo=False)
        await db.commit()

        users = [
            {"login": "ops_a", "uid": 1001, "shell": "/bin/bash",
             "home_dir": "/home/ops_a", "unix_groups": [], "has_sudo": False},
            {"login": "ops_b", "uid": 1002, "shell": "/bin/bash",
             "home_dir": "/home/ops_b", "unix_groups": [], "has_sudo": False},
            {"login": "ops_c", "uid": 1003, "shell": "/bin/bash",
             "home_dir": "/home/ops_c", "unix_groups": [], "has_sudo": False},
            {"login": "ops_d", "uid": 1004, "shell": "/bin/bash",
             "home_dir": "/home/ops_d", "unix_groups": [], "has_sudo": False},
            {"login": "ghost_1", "uid": 1500, "shell": "/bin/bash",
             "home_dir": "/home/ghost_1", "unix_groups": [], "has_sudo": False},
            {"login": "ghost_2", "uid": 1501, "shell": "/bin/bash",
             "home_dir": "/home/ghost_2", "unix_groups": [], "has_sudo": False},
        ]

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a),
            json={"users": users},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["present"] == 4
        # Незнакомые ghost_1/ghost_2 аккаунтами не заводятся — уходят в
        # unknown_users, created остаётся нулём.
        assert body["created"] == 0
        assert {u["login"] for u in body["unknown_users"]} == {
            "ghost_1", "ghost_2",
        }

        # Reconcile грузит аккаунты по login'ам ОДИН раз и связки по
        # account_id'ам ОДИН раз; per-юзер lookup'ы в основном цикле не нужны.
        assert reconcile_call_spy["list_accounts_on_server_by_logins"] == 1
        assert reconcile_call_spy["list_links_for_server_by_account_ids"] == 1
        assert reconcile_call_spy["get_account_on_server_by_login"] == 0, (
            "per-login SELECT просочился в основной reconcile-цикл: "
            f"{reconcile_call_spy['get_account_on_server_by_login']}"
        )
        assert reconcile_call_spy["get_link"] == 0, (
            "per-account get_link просочился в основной reconcile-цикл: "
            f"{reconcile_call_spy['get_link']}"
        )

    async def test_reconcile_empty_payload_no_per_user_queries(
        self, client, worker_bot_token_a, make_server, make_account,
        db, dept_a, reconcile_call_spy,
    ):
        """Пустой `users=[]` → ни одного per-юзер lookup'а.

        Batch-методы вызываются с пустым списком — реализация в репозитории
        замыкает на ранний return и SQL не идёт, но интерфейсный контракт
        «один вызов на reconcile» сохраняется. Сами cached helpers инкрементят
        счётчик независимо от того, упёрлись ли в early-return внутри.
        """
        srv = await make_server(department_id=dept_a)
        await make_account(server_id=srv.id, login="orphan")
        await db.commit()

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a),
            json={"users": []},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # `orphan` привязан в БД, но не найден на боксе — missing drift.
        assert body["created"] == 0
        assert body["present"] == 0
        assert body["drifted"] == 1

        assert reconcile_call_spy["get_account_on_server_by_login"] == 0
        assert reconcile_call_spy["get_link"] == 0
