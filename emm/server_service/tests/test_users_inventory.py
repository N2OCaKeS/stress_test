"""Тесты инвентаризации OS-пользователей.

Покрывает:
* `POST /servers/{id}/users/inventory` (user-trigger) — 202 + task_id,
  permissions/visibility, decommissioned;
* `POST /internal/servers/{id}/users/inventory` (worker callback) — reconcile:
  незнакомый юзер уходит в `unknown_users` (discovered НЕ создаётся, но drift
  поднимается) / confirm present / warn-on-drift по атрибутам (БД не
  перетирается) / mark missing (drift) / ignore-list пропускается; права
  (worker_bot может, reader нет).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.constants import ServerStatus
from src.models import ServerAccount, ServerAccountServer

BASE = "/api/server/v1/servers"
BASE_INT = "/api/server/v1/internal"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task из endpoints/inventory.py."""
    calls: list[dict] = []
    by_key: dict[str, str] = {}

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0,
                            return_hit=False):
        if idempotency_key is not None and idempotency_key in by_key:
            existing = by_key[idempotency_key]
            return (existing, True) if return_hit else existing
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "target_resource_id": target_resource_id,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if idempotency_key is not None:
            by_key[idempotency_key] = new_id
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    # Сам dispatch живёт в общей обвязке `endpoints/_dispatch.py`
    # (`dispatch_server_ssh_task`); патчим shared `worker_client` через её
    # ссылку — она тот же модуль-объект, что и в endpoint'е.
    monkeypatch.setattr(
        "src.api.v1.endpoints._dispatch.worker_client.dispatch_task", fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints._dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


@pytest.fixture
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.inventory.audit_service.emit",
        "src.services.internal_service.audit_service.emit",
    )


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


# ── Trigger: POST /servers/{id}/users/inventory ──────────────────────────────


async def _prepared(db, srv, management_user="dbos"):
    """Пометить сервер подготовленным к управлению (после prepare).

    Инвентаризация требует `is_managed=True` — worker заходит по ключу под
    управляющим пользователем. Без prepare endpoint отдаёт 409 PREPARE_REQUIRED.
    """
    srv.is_managed = True
    srv.management_user = management_user
    await db.flush()
    return srv


class TestUsersInventoryTrigger:
    async def test_operator_dispatches(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"].startswith("tsk_")
        assert body["status"] == "queued"
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["task_kind"] == "users.inventory"
        # Managed-сервер — вход по ключу, аккаунта в payload нет.
        assert captured_dispatch[0]["target_resource_id"] is None
        assert captured_dispatch[0]["payload"] == {
            "server_id": srv.id,
            "target_department_id": "dep_a",
            "host": str(srv.ip_address),
            "ssh_port": srv.ssh_port,
            "is_managed": True,
            "management_user": "dbos",
        }

    async def test_unprepared_server_returns_prepare_required(
        self, client, operator_token_a, make_server, make_account, captured_dispatch,
    ):
        """Неподготовленный сервер → 409 PREPARE_REQUIRED, dispatch не идёт."""
        srv = await make_server(department_id="dep_a")
        # Даже с привязанным аккаунтом без prepare инвентаризация не запускается.
        await make_account(server_id=srv.id, login="appuser")
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "PREPARE_REQUIRED")
        assert captured_dispatch == []

    async def test_managed_server_propagates_session_hints(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        call = captured_dispatch[0]
        payload = call["payload"]
        assert payload["is_managed"] is True
        assert payload["management_user"] == "dbos"
        # Managed-сервер — вход по ключу, аккаунта нет → колонка null.
        assert call["target_resource_id"] is None

    async def test_dispatch_payload_includes_host_and_port(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        # Симметрия с `_dispatch_for_server`: dispatch payload должен нести
        # `host`/`ssh_port`, иначе SSH-клиент воркера фоллбэкается на
        # `server_id` (UUID) и ходит в несуществующий хост. В host едет IP,
        # а не hostname — короткие имена не резолвятся из пода воркера.
        srv = await make_server(
            department_id="dep_a",
            hostname="srv-with-host.example.local",
            ip_address="10.177.103.155",
        )
        await _prepared(db, srv)
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["host"] == "10.177.103.155"
        assert payload["host"] != srv.id
        assert payload["ssh_port"] == srv.ssh_port

    async def test_reader_cannot_trigger(
        self, client, reader_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_b),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert captured_dispatch == []

    async def test_no_token_returns_401(self, client, make_server, captured_dispatch):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(f"{BASE}/{srv.id}/users/inventory")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
        assert captured_dispatch == []

    async def test_decommissioned_returns_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")
        assert captured_dispatch == []

    async def test_success_emits_audit(
        self, client, operator_token_a, make_server,
        captured_dispatch, captured_emits, db,
    ):
        srv = await make_server(department_id="dep_a")
        await _prepared(db, srv)
        await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_a),
        )
        ok = [e for e in _events(captured_emits, "server.users_inventory_triggered")
              if e["status"] == "success"]
        assert len(ok) == 1
        assert ok[0]["details"]["task_kind"] == "users.inventory"


# ── Callback reconcile: POST /internal/servers/{id}/users/inventory ──────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestUsersInventoryReconcile:
    async def test_unknown_user_not_created_goes_to_unknown_users(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_emits,
    ):
        srv = await make_server(department_id=dept_a)
        payload = {"users": [
            {"login": "ops", "uid": 1001, "shell": "/bin/bash",
             "home_dir": "/home/ops", "unix_groups": ["sudo"], "has_sudo": True},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # discovered-аккаунт больше НЕ создаётся автоматически.
        assert body["created"] == 0
        assert body["present"] == 0
        # Юзер на боксе без аккаунта в БД — drift-сигнал + попадает в unknown_users.
        assert body["drifted"] == 1
        assert len(body["unknown_users"]) == 1
        u = body["unknown_users"][0]
        assert u["login"] == "ops"
        assert u["uid"] == 1001
        assert u["has_sudo"] is True
        assert u["unix_groups"] == ["sudo"]
        assert u["shell"] == "/bin/bash"

        drift = _events(captured_emits, "server_account.drift_detected")
        assert len(drift) == 1
        assert drift[0]["details"]["drift"] == "unknown_login"
        assert drift[0]["details"]["login"] == "ops"
        assert drift[0]["status"] == "warning"

        await db.commit()
        # Никакой аккаунт в БД не заведён.
        acc = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "ops")
        )).scalar_one_or_none()
        assert acc is None

    async def test_repeated_callback_keeps_unknown_user_unknown(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        # Повторный callback с тем же незнакомым юзером — он по-прежнему просто
        # в unknown_users, без создания аккаунта и без 500.
        srv = await make_server(department_id=dept_a)
        payload = {"users": [
            {"login": "dup_user", "uid": 1500, "shell": "/bin/bash",
             "home_dir": "/home/dup_user", "unix_groups": [], "has_sudo": False},
        ]}
        first = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert first.status_code == 200, first.text
        assert first.json()["created"] == 0
        assert len(first.json()["unknown_users"]) == 1

        second = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["created"] == 0
        assert body["present"] == 0
        assert len(body["unknown_users"]) == 1

        await db.commit()
        accs = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "dup_user")
        )).scalars().all()
        # Аккаунта нет вовсе — авто-создание убрано.
        assert len(accs) == 0

    async def test_attribute_drift_warns_and_keeps_db(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(
            server_id=srv.id, login="postgres", has_sudo=False,
            shell="/bin/bash", home_dir="/home/postgres", unix_groups=["postgres"],
        )
        # На боксе атрибуты разошлись с БД — БД истина, поля НЕ перетираем.
        payload = {"users": [
            {"login": "postgres", "uid": 1100, "shell": "/bin/sh",
             "home_dir": "/var/lib/postgresql", "unix_groups": ["wheel"],
             "has_sudo": True},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["present"] == 1
        assert body["created"] == 0
        assert body["drifted"] == 1

        drift = _events(captured_emits, "server_account.drift_detected")
        assert len(drift) == 1
        d = drift[0]["details"]
        assert d["drift"] == "attributes"
        assert d["login"] == "postgres"
        # home_dir намеренно НЕ в `_DRIFT_ATTRS` (см. internal_service.py) —
        # PATCH home_dir не запускает fan-out, и эмитить drift на каждом скане
        # смысла нет (оператор не может его закрыть API-действием).
        assert set(d["fields"]) == {"has_sudo", "unix_groups", "shell"}
        assert d["expected"]["has_sudo"] is False
        assert d["found"]["has_sudo"] is True
        assert d["expected"]["shell"] == "/bin/bash"
        assert d["found"]["shell"] == "/bin/sh"
        assert drift[0]["status"] == "warning"

        await db.commit()
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        # БД НЕ перетёрта — целевое состояние сохранено.
        assert refreshed.has_sudo is False
        assert refreshed.shell == "/bin/bash"
        assert refreshed.home_dir == "/home/postgres"
        assert refreshed.unix_groups == ["postgres"]
        assert refreshed.password_encrypted is not None
        assert refreshed.source == "managed"
        # Связка помечена present + свежий last_inventory_at.
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        assert link.present_on_server is True
        assert link.last_inventory_at is not None

    async def test_attribute_drift_returns_structured_diffs(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        """Ответ reconcile несёт per-account `diffs` с expected/found — данные
        для UI-ревью drift'а. БД при этом НЕ перетирается."""
        srv = await make_server(department_id=dept_a)
        acc = await make_account(
            server_id=srv.id, login="postgres", has_sudo=False,
            shell="/bin/bash", home_dir="/home/postgres", unix_groups=["postgres"],
        )
        payload = {"users": [
            {"login": "postgres", "uid": 1100, "shell": "/bin/sh",
             "home_dir": "/var/lib/postgresql", "unix_groups": ["wheel"],
             "has_sudo": True},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        diffs = body["diffs"]
        assert len(diffs) == 1
        d = diffs[0]
        assert d["account_id"] == acc.id
        assert d["login"] == "postgres"
        assert set(d["fields"]) == {"has_sudo", "unix_groups", "shell"}
        assert d["fields"]["has_sudo"] == {"expected": False, "found": True}
        assert d["fields"]["shell"] == {"expected": "/bin/bash", "found": "/bin/sh"}
        assert d["fields"]["unix_groups"]["expected"] == ["postgres"]
        assert d["fields"]["unix_groups"]["found"] == ["wheel"]

    async def test_unknown_login_returns_empty_diffs(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        """Незнакомый юзер (unknown_login) — drift + unknown_users, но не
        attribute-diff: в `diffs` его быть не должно (там только привязанные
        аккаунты с расхождением атрибутов)."""
        srv = await make_server(department_id=dept_a)
        payload = {"users": [
            {"login": "ops", "uid": 1001, "shell": "/bin/bash",
             "home_dir": "/home/ops", "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["diffs"] == []
        assert len(body["unknown_users"]) == 1

    async def test_matching_attributes_no_drift(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(
            server_id=srv.id, login="app", has_sudo=True,
            shell="/bin/bash", home_dir="/home/app", unix_groups=["sudo", "app"],
        )
        # Бокс совпадает с БД (группы как множество) — present без drift.
        payload = {"users": [
            {"login": "app", "uid": 1200, "shell": "/bin/bash",
             "home_dir": "/home/app", "unix_groups": ["app", "sudo"],
             "has_sudo": True},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["present"] == 1
        assert body["drifted"] == 0
        assert _events(captured_emits, "server_account.drift_detected") == []

        await db.commit()
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        assert link.present_on_server is True
        assert link.last_inventory_at is not None

    async def test_missing_account_marked_drift(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="ghost")
        # Пустой список — пользователя на сервере больше нет.
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json={"users": []},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["drifted"] == 1

        drift = _events(captured_emits, "server_account.drift_detected")
        assert len(drift) == 1
        assert drift[0]["details"]["drift"] == "missing_on_box"
        assert drift[0]["details"]["login"] == "ghost"
        assert drift[0]["status"] == "warning"

        await db.commit()
        # Аккаунт НЕ удалён, связка помечена отсутствующей.
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        assert link.present_on_server is False
        assert link.last_inventory_at is not None
        still_there = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one_or_none()
        assert still_there is not None

    async def test_mixed_create_present_drift(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        srv = await make_server(department_id=dept_a)
        # keep: атрибуты бокса совпадут с дефолтами аккаунта → present без drift.
        await make_account(server_id=srv.id, login="keep")
        await make_account(server_id=srv.id, login="gone")
        payload = {"users": [
            {"login": "keep", "uid": 1001},   # present, без drift
            {"login": "fresh", "uid": 1002},  # unknown → drift + unknown_users
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        body = resp.json()
        # discovered-аккаунт не создаётся — fresh уходит в unknown_users.
        assert body["created"] == 0
        assert body["present"] == 1
        assert {u["login"] for u in body["unknown_users"]} == {"fresh"}
        # fresh (unknown_login) + gone (missing_on_box) — два drift-сигнала.
        assert body["drifted"] == 2
        drift = _events(captured_emits, "server_account.drift_detected")
        kinds = {(e["details"]["login"], e["details"]["drift"]) for e in drift}
        assert kinds == {("fresh", "unknown_login"), ("gone", "missing_on_box")}

    async def test_reader_cannot_submit(
        self, client, reader_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(reader_token_a), json={"users": []},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_worker_bot_can_submit(
        self, client, worker_bot_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json={"users": []},
        )
        assert resp.status_code == 200

    async def test_nonexistent_server_404(
        self, client, worker_bot_token_a,
    ):
        resp = await client.post(
            f"{BASE_INT}/servers/srv_ghost/users/inventory",
            headers=_hdr(worker_bot_token_a), json={"users": []},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_reconcile_uses_batched_queries(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        monkeypatch,
    ):
        """Reconcile-цикл по N юзерам не должен делать 2·N запросов в репо.

        Раньше get_account_on_server_by_login + get_link дёргались на каждого
        юзера. Сейчас вызовы заменены батч-методами `list_accounts_on_server_by_logins`
        + `list_links_for_server_by_account_ids` — фиксированное число запросов
        вне зависимости от N.
        """
        from src.repositories import server_account as repo

        per_user: list[str] = []
        batch: list[str] = []

        original_by_login = repo.get_account_on_server_by_login
        original_get_link = repo.get_link
        original_batch_login = repo.list_accounts_on_server_by_logins
        original_batch_links = repo.list_links_for_server_by_account_ids

        async def track_by_login(*args, **kwargs):
            per_user.append("get_account_on_server_by_login")
            return await original_by_login(*args, **kwargs)

        async def track_get_link(*args, **kwargs):
            per_user.append("get_link")
            return await original_get_link(*args, **kwargs)

        async def track_batch_login(*args, **kwargs):
            batch.append("list_accounts_on_server_by_logins")
            return await original_batch_login(*args, **kwargs)

        async def track_batch_links(*args, **kwargs):
            batch.append("list_links_for_server_by_account_ids")
            return await original_batch_links(*args, **kwargs)

        monkeypatch.setattr(
            "src.services.internal_service.account_repo.get_account_on_server_by_login",
            track_by_login,
        )
        monkeypatch.setattr(
            "src.services.internal_service.account_repo.get_link", track_get_link,
        )
        monkeypatch.setattr(
            "src.services.internal_service.account_repo.list_accounts_on_server_by_logins",
            track_batch_login,
        )
        monkeypatch.setattr(
            "src.services.internal_service.account_repo.list_links_for_server_by_account_ids",
            track_batch_links,
        )

        srv = await make_server(department_id=dept_a)
        for login in ("a", "b", "c", "d", "e"):
            await make_account(server_id=srv.id, login=login)
        await db.commit()

        users = [{"login": ch, "uid": 1000 + i} for i, ch in enumerate("abcde")]
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json={"users": users},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["present"] == 5

        # Без батча было бы по 1+ вызову на юзера — теперь должно быть 0.
        assert per_user == [], f"per-user queries leaked: {per_user}"
        assert "list_accounts_on_server_by_logins" in batch
        assert "list_links_for_server_by_account_ids" in batch
        # Каждый батч-метод вызывается ровно один раз на reconcile.
        assert batch.count("list_accounts_on_server_by_logins") == 1
        assert batch.count("list_links_for_server_by_account_ids") == 1

    async def test_auto_create_os_emits_warning(
        self, client, worker_bot_token_a, make_server, dept_a, captured_emits,
    ):
        """Имя, прошедшее whitelist (KNOWN_OS_PREFIXES), но отсутствующее в
        каталоге — заводит запись + поднимает WARNING-аудит
        `os_version.create` с `reason=auto_from_inventory`. SOC видит, кто
        загрязнил каталог. Имена вне whitelist'а покрыты отдельным тестом
        в `test_os_whitelist.py` (там запись НЕ создаётся и эмитится
        `os.unknown_observed`)."""
        srv = await make_server(department_id=dept_a)
        payload = {
            "hostname": "srv-warn",
            "kernel": "5.10.0",
            "cpu_brand": None,
            "cpu_model": None,
            "cpu_cores": 1,
            "cpu_threads": None,
            "cpu_frequency_ghz": None,
            # известный prefix "Astra Linux", версии 99.99 нет в seed'е → first-seen
            "os_version": "Astra Linux SE 99.99",
            "disks": [],
        }
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text

        warns = [
            e for e in captured_emits
            if e["action"] == "os_version.create" and e.get("status") == "warning"
        ]
        assert warns, f"expected os_version.create warning, captured: {captured_emits}"
        emit = warns[0]
        details = emit.get("details") or {}
        assert details.get("name") == "Astra Linux SE 99.99"
        assert details.get("reason") == "auto_from_inventory"
        assert details.get("server_id") == srv.id
        assert details.get("server_department_id") == dept_a

    async def test_auto_create_os_rejects_invalid_name(
        self, client, worker_bot_token_a, make_server, dept_a,
    ):
        """Имя ОС с управляющими/мусорными символами отбивается 422 ещё на
        схеме — даже если worker_bot скомпрометирован."""
        srv = await make_server(department_id=dept_a)
        payload = {
            "hostname": "srv-evil",
            "kernel": "5.10.0",
            "cpu_brand": None,
            "cpu_model": None,
            "cpu_cores": 1,
            "cpu_threads": None,
            "cpu_frequency_ghz": None,
            "os_version": "Astra; DROP TABLE os_versions;--",
            "disks": [],
        }
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert_error(resp, 422, "VALIDATION_ERROR")


@pytest.mark.usefixtures("soft_dept_mode")
class TestUnlinkedExistingClassification:
    """Логины с бокса, под которые в отделе уже есть аккаунт, но он не привязан
    к инвентаризуемому серверу, попадают в отдельную категорию `unlinked_existing`,
    а не в `unknown_users`. Reconcile ничего не создаёт и не линкует."""

    async def test_existing_unlinked_account_goes_to_unlinked_existing(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        # Аккаунт "deploy" привязан к ДРУГОМУ серверу того же отдела.
        other = await make_server(department_id=dept_a, hostname="other.local")
        acc = await make_account(server_id=other.id, login="deploy")
        target = await make_server(department_id=dept_a, hostname="target.local")
        await db.commit()

        payload = {"users": [
            {"login": "deploy", "uid": 1400, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{target.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Не unknown, не present — отдельная категория.
        assert body["unknown_users"] == []
        assert body["present"] == 0
        assert body["created"] == 0
        # unlinked_existing не дрейфит.
        assert body["drifted"] == 0
        assert len(body["unlinked_existing"]) == 1
        item = body["unlinked_existing"][0]
        assert item["login"] == "deploy"
        assert item["uid"] == 1400
        assert len(item["candidates"]) == 1
        cand = item["candidates"][0]
        assert cand["account_id"] == acc.id
        assert cand["department_id"] == dept_a
        assert cand["source"] == "managed"

        # Drift unknown_login на этот логин НЕ эмитится.
        drift_logins = {
            e["details"]["login"]
            for e in _events(captured_emits, "server_account.drift_detected")
        }
        assert "deploy" not in drift_logins

        await db.commit()
        # Ничего не создано и не привязано к target.
        accs = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "deploy")
        )).scalars().all()
        assert len(accs) == 1
        links = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == target.id,
            )
        )).scalars().all()
        assert links == []

    async def test_truly_unknown_login_stays_in_unknown_users(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        # Под этот login в отделе аккаунта нет вовсе → unknown_users.
        srv = await make_server(department_id=dept_a)
        payload = {"users": [
            {"login": "nobodyhere", "uid": 1401, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {u["login"] for u in body["unknown_users"]} == {"nobodyhere"}
        assert body["unlinked_existing"] == []
        assert body["drifted"] == 1

    async def test_already_linked_account_in_neither_category(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
    ):
        # Аккаунт уже привязан к ЭТОМУ серверу → present, не в unknown и не в
        # unlinked_existing.
        srv = await make_server(department_id=dept_a)
        await make_account(server_id=srv.id, login="app")
        await db.commit()
        payload = {"users": [
            {"login": "app", "uid": 1402, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["present"] == 1
        assert body["unknown_users"] == []
        assert body["unlinked_existing"] == []

    async def test_multiple_candidates_returned_as_list(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
    ):
        # Login не уникален в отделе — два аккаунта "svc" на разных серверах.
        # Оба кандидаты на связку с target → список, без падения.
        s1 = await make_server(department_id=dept_a, hostname="s1.local")
        s2 = await make_server(department_id=dept_a, hostname="s2.local")
        a1 = await make_account(server_id=s1.id, login="svc")
        a2 = await make_account(server_id=s2.id, login="svc")
        target = await make_server(department_id=dept_a, hostname="t.local")
        await db.commit()

        payload = {"users": [
            {"login": "svc", "uid": 1403, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{target.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["unknown_users"] == []
        assert len(body["unlinked_existing"]) == 1
        item = body["unlinked_existing"][0]
        assert item["login"] == "svc"
        ids = {c["account_id"] for c in item["candidates"]}
        assert ids == {a1.id, a2.id}

    async def test_cross_department_account_not_a_candidate(
        self, client, worker_bot_token_a, make_server, make_account, db,
        dept_a, dept_b,
    ):
        # Аккаунт "shared" живёт в dept_b. Инвентаризуем сервер dept_a с тем же
        # login'ом → это НЕ кандидат (другой отдел) → unknown_users.
        srv_b = await make_server(department_id=dept_b, hostname="b.local")
        await make_account(server_id=srv_b.id, login="shared")
        target = await make_server(department_id=dept_a, hostname="a.local")
        await db.commit()

        payload = {"users": [
            {"login": "shared", "uid": 1404, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{target.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {u["login"] for u in body["unknown_users"]} == {"shared"}
        assert body["unlinked_existing"] == []
        assert body["drifted"] == 1

    async def test_ignored_login_not_in_unlinked_existing(
        self, client, worker_bot_token_a, admin_role_token_a, make_server,
        make_account, db, dept_a,
    ):
        # Заигнорённый логин пропускается целиком, даже если под него в отделе
        # есть непривязанный аккаунт.
        other = await make_server(department_id=dept_a, hostname="o.local")
        await make_account(server_id=other.id, login="monitoring")
        target = await make_server(department_id=dept_a, hostname="tt.local")
        ign = await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(admin_role_token_a), json={"login": "monitoring"},
        )
        assert ign.status_code == 201, ign.text
        await db.commit()

        payload = {"users": [
            {"login": "monitoring", "uid": 1405, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{target.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["unknown_users"] == []
        assert body["unlinked_existing"] == []
        assert body["drifted"] == 0


ACC_BASE = "/api/server/v1/server-accounts"


@pytest.mark.usefixtures("soft_dept_mode")
class TestImportUnknownUser:
    """Импорт незнакомого юзера из обзора инвентаризации (`unknown_users`)."""

    async def test_import_creates_discovered_account_present_on_server(
        self, client, admin_role_token_a, make_server, db, dept_a, captured_emits,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{ACC_BASE}/import",
            headers=_hdr(admin_role_token_a),
            json={
                "server_id": srv.id, "login": "ops",
                "has_sudo": True, "unix_groups": ["sudo"], "shell": "/bin/bash",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["login"] == "ops"
        assert body["source"] == "discovered"
        assert body["has_sudo"] is True
        assert body["server_ids"] == [srv.id]
        assert body["password_b64"] is None

        await db.commit()
        acc = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "ops")
        )).scalar_one()
        assert acc.source == "discovered"
        assert acc.password_encrypted is None
        assert acc.department_id == dept_a
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        # Пользователь уже на боксе — связка present.
        assert link.present_on_server is True
        assert link.last_inventory_at is not None

        emits = _events(captured_emits, "server_account.imported_from_host")
        assert any(e["status"] == "success" for e in emits)

    async def test_import_default_source_is_discovered(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{ACC_BASE}/import",
            headers=_hdr(admin_role_token_a),
            json={"server_id": srv.id, "login": "deploy"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["source"] == "discovered"

    async def test_import_duplicate_login_409(
        self, client, admin_role_token_a, make_server, make_account, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        await make_account(server_id=srv.id, login="taken")
        resp = await client.post(
            f"{ACC_BASE}/import",
            headers=_hdr(admin_role_token_a),
            json={"server_id": srv.id, "login": "taken"},
        )
        assert_error(resp, 409, "ACCOUNT_DUPLICATE")

    async def test_import_cross_dept_server_404(
        self, client, operator_token_b, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{ACC_BASE}/import",
            headers=_hdr(operator_token_b),
            json={"server_id": srv.id, "login": "ops"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_import_reader_cannot(
        self, client, reader_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{ACC_BASE}/import",
            headers=_hdr(reader_token_a),
            json={"server_id": srv.id, "login": "ops"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_import_sudo_requires_grant_sudo(
        self, client, operator_token_a, make_server, db, dept_a,
    ):
        # operator держит create, но не grant_sudo — has_sudo=True → 403.
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{ACC_BASE}/import",
            headers=_hdr(operator_token_a),
            json={"server_id": srv.id, "login": "ops", "has_sudo": True},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_imported_discovered_has_no_password_in_card(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        created = await client.post(
            f"{ACC_BASE}/import",
            headers=_hdr(admin_role_token_a),
            json={"server_id": srv.id, "login": "ops"},
        )
        acc_id = created.json()["id"]
        # GET с view_password не падает — пароля нет, password_b64 = null.
        resp = await client.get(
            f"{ACC_BASE}/{acc_id}", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "discovered"
        assert body["password_b64"] is None


@pytest.mark.usefixtures("soft_dept_mode")
class TestIgnoredLoginsCrud:
    """CRUD ignore-list'а логинов + dept-изоляция + RBAC."""

    async def test_add_list_remove(
        self, client, admin_role_token_a, dept_a, captured_emits,
    ):
        add = await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(admin_role_token_a),
            json={"login": "nobody", "reason": "служебная учётка"},
        )
        assert add.status_code == 201, add.text
        body = add.json()
        assert body["login"] == "nobody"
        assert body["reason"] == "служебная учётка"
        assert body["department_id"] == dept_a

        listing = await client.get(
            f"{ACC_BASE}/ignored-logins", headers=_hdr(admin_role_token_a),
        )
        assert listing.status_code == 200, listing.text
        logins = [i["login"] for i in listing.json()]
        assert logins == ["nobody"]

        rm = await client.delete(
            f"{ACC_BASE}/ignored-logins/nobody",
            headers=_hdr(admin_role_token_a),
        )
        assert rm.status_code == 200, rm.text

        listing2 = await client.get(
            f"{ACC_BASE}/ignored-logins", headers=_hdr(admin_role_token_a),
        )
        assert listing2.json() == []

        added = _events(captured_emits, "server_account.ignored_login_added")
        removed = _events(captured_emits, "server_account.ignored_login_removed")
        assert any(e["status"] == "success" for e in added)
        assert any(e["status"] == "success" for e in removed)

    async def test_duplicate_login_409(
        self, client, admin_role_token_a, dept_a,
    ):
        first = await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(admin_role_token_a), json={"login": "svc"},
        )
        assert first.status_code == 201
        second = await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(admin_role_token_a), json={"login": "svc"},
        )
        assert_error(second, 409, "IGNORED_LOGIN_DUPLICATE")

    async def test_remove_missing_404(
        self, client, admin_role_token_a, dept_a,
    ):
        resp = await client.delete(
            f"{ACC_BASE}/ignored-logins/ghost",
            headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 404, "IGNORED_LOGIN_NOT_FOUND")

    async def test_dept_isolation(
        self, client, admin_role_token_a, admin_token_b, dept_a, dept_b,
    ):
        # dep_a добавляет логин — dep_b его не видит.
        await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(admin_role_token_a), json={"login": "only_a"},
        )
        b_list = await client.get(
            f"{ACC_BASE}/ignored-logins", headers=_hdr(admin_token_b),
        )
        assert b_list.status_code == 200, b_list.text
        assert [i["login"] for i in b_list.json()] == []

        # Один и тот же логин в обоих отделах не конфликтует (scope=dept).
        b_add = await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(admin_token_b), json={"login": "only_a"},
        )
        assert b_add.status_code == 201, b_add.text

        # dep_b снимает свой — у dep_a остаётся.
        await client.delete(
            f"{ACC_BASE}/ignored-logins/only_a", headers=_hdr(admin_token_b),
        )
        a_list = await client.get(
            f"{ACC_BASE}/ignored-logins", headers=_hdr(admin_role_token_a),
        )
        assert [i["login"] for i in a_list.json()] == ["only_a"]

    async def test_reader_cannot_manage(
        self, client, reader_token_a, dept_a,
    ):
        add = await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(reader_token_a), json={"login": "x"},
        )
        assert_error(add, 403, "PERMISSION_DENIED")
        listing = await client.get(
            f"{ACC_BASE}/ignored-logins", headers=_hdr(reader_token_a),
        )
        assert_error(listing, 403, "PERMISSION_DENIED")

    async def test_operator_can_manage(
        self, client, operator_token_a, dept_a,
    ):
        add = await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(operator_token_a), json={"login": "opsvc"},
        )
        assert add.status_code == 201, add.text


@pytest.mark.usefixtures("soft_dept_mode")
class TestReconcileRespectsIgnoreList:
    async def test_ignored_login_skipped_entirely(
        self, client, worker_bot_token_a, admin_role_token_a, make_server,
        db, dept_a, captured_emits,
    ):
        srv = await make_server(department_id=dept_a)
        # Заигнорить "monitoring" в отделе.
        ign = await client.post(
            f"{ACC_BASE}/ignored-logins",
            headers=_hdr(admin_role_token_a), json={"login": "monitoring"},
        )
        assert ign.status_code == 201, ign.text

        payload = {"users": [
            {"login": "monitoring", "uid": 1300, "unix_groups": [], "has_sudo": False},
            {"login": "ops", "uid": 1301, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # monitoring — заигнорен (не в unknown_users, не дрейфит); ops — нет.
        assert {u["login"] for u in body["unknown_users"]} == {"ops"}
        assert body["drifted"] == 1
        drift_logins = {
            e["details"]["login"]
            for e in _events(captured_emits, "server_account.drift_detected")
        }
        assert drift_logins == {"ops"}


@pytest.mark.usefixtures("soft_dept_mode")
class TestReconcileIgnoresManagementUser:
    async def test_management_user_not_classified(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_emits,
    ):
        # Управляющая учётка сервера ("dbos") приходит в инвентаризации, но это
        # наш SSH-пользователь — он не должен попасть ни в unknown_users, ни в
        # drift. Обычный незнакомый "ops" классифицируется как раньше.
        srv = await make_server(department_id=dept_a)
        srv.management_user = "dbos"
        await db.flush()

        payload = {"users": [
            {"login": "dbos", "uid": 1000, "unix_groups": ["sudo"], "has_sudo": True},
            {"login": "ops", "uid": 1001, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # dbos — управляющая, проигнорирована; ops — настоящий unknown.
        assert {u["login"] for u in body["unknown_users"]} == {"ops"}
        assert body["drifted"] == 1
        drift_logins = {
            e["details"]["login"]
            for e in _events(captured_emits, "server_account.drift_detected")
        }
        assert drift_logins == {"ops"}

    async def test_management_user_with_existing_account_no_drift(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        captured_emits,
    ):
        # Даже если под управляющий логин в БД есть привязанный аккаунт с
        # разошедшимися атрибутами — drift на него не поднимаем, учётка наша.
        srv = await make_server(department_id=dept_a)
        srv.management_user = "dbos"
        await make_account(
            server_id=srv.id, login="dbos", has_sudo=False,
            shell="/bin/bash", home_dir="/home/dbos", unix_groups=[],
        )
        await db.flush()

        payload = {"users": [
            {"login": "dbos", "uid": 1000, "shell": "/bin/sh",
             "home_dir": "/home/dbos", "unix_groups": ["wheel"], "has_sudo": True},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["drifted"] == 0
        assert body["unknown_users"] == []
        assert body["unlinked_existing"] == []
        assert _events(captured_emits, "server_account.drift_detected") == []

    async def test_no_management_user_behaves_as_before(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_emits,
    ):
        # management_user=None → старое поведение: dbos классифицируется как
        # обычный неизвестный логин.
        srv = await make_server(department_id=dept_a)
        assert srv.management_user is None

        payload = {"users": [
            {"login": "dbos", "uid": 1000, "unix_groups": [], "has_sudo": False},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {u["login"] for u in body["unknown_users"]} == {"dbos"}
        assert body["drifted"] == 1
        drift_logins = {
            e["details"]["login"]
            for e in _events(captured_emits, "server_account.drift_detected")
        }
        assert drift_logins == {"dbos"}
