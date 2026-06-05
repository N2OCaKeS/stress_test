"""Тесты инвентаризации OS-пользователей.

Покрывает:
* `POST /servers/{id}/users/inventory` (user-trigger) — 202 + task_id,
  permissions/visibility, decommissioned;
* `POST /internal/servers/{id}/users/inventory` (worker callback) — reconcile:
  create discovered (drift) / confirm present / warn-on-drift по атрибутам
  (БД не перетирается) / mark missing (drift); права (worker_bot может,
  reader нет); discovered-аккаунт без пароля.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.constants import ServerStatus
from src.models import ServerAccount, ServerAccountServer

BASE = "/api/server/v1/servers"
BASE_INT = "/api/server/v1/internal"


def _hdr(token: str, dept: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if dept is not None:
        headers["X-Target-Department-Id"] = dept
    return headers


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task из endpoints/inventory.py."""
    calls: list[dict] = []
    by_key: dict[str, str] = {}

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        if idempotency_key is not None and idempotency_key in by_key:
            existing = by_key[idempotency_key]
            return (existing, True) if return_hit else existing
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if idempotency_key is not None:
            by_key[idempotency_key] = new_id
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    monkeypatch.setattr(
        "src.api.v1.endpoints.inventory.worker_client.dispatch_task", fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.inventory.worker_client.dispatch_task_with_hit",
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


class TestUsersInventoryTrigger:
    async def test_operator_dispatches(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"].startswith("tsk_")
        assert body["status"] == "queued"
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["task_kind"] == "users.inventory"
        assert captured_dispatch[0]["payload"] == {
            "server_id": srv.id,
            "target_department_id": "dep_a",
            "is_managed": False,
            "management_user": None,
        }

    async def test_managed_server_propagates_session_hints(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.is_managed = True
        srv.management_user = "dbos"
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        payload = captured_dispatch[0]["payload"]
        assert payload["is_managed"] is True
        assert payload["management_user"] == "dbos"

    async def test_reader_cannot_trigger(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/users/inventory", headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404
        assert captured_dispatch == []

    async def test_no_token_returns_401(self, client, make_server, captured_dispatch):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(f"{BASE}/{srv.id}/users/inventory")
        assert resp.status_code == 401
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
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []

    async def test_success_emits_audit(
        self, client, operator_token_a, make_server, captured_dispatch, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
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
    async def test_creates_discovered_account(
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
        assert body["created"] == 1
        assert body["present"] == 0
        # Юзер на боксе без аккаунта в БД — drift-сигнал.
        assert body["drifted"] == 1
        drift = _events(captured_emits, "server_account.drift_detected")
        assert len(drift) == 1
        assert drift[0]["details"]["drift"] == "unknown_login"
        assert drift[0]["details"]["login"] == "ops"
        assert drift[0]["status"] == "warning"

        await db.commit()
        acc = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "ops")
        )).scalar_one()
        assert acc.source == "discovered"
        assert acc.password_encrypted is None
        assert acc.department_id == dept_a
        assert acc.has_sudo is True
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        assert link.present_on_server is True
        assert link.last_inventory_at is not None

    async def test_duplicate_callback_is_idempotent(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        # Worker может повторить callback после HTTP-таймаута — первый завёл
        # discovered-аккаунт, второй раз приходит тот же payload и должен
        # пройти без 500 (IntegrityError на uq_server_login).
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
        assert first.json()["created"] == 1

        second = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        assert second.status_code == 200, second.text
        body = second.json()
        # Повтор не создаёт нового аккаунта и не валит 500.
        assert body["created"] == 0
        assert body["present"] == 1

        await db.commit()
        accs = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "dup_user")
        )).scalars().all()
        # Один аккаунт в БД — повтор не задвоил строку.
        assert len(accs) == 1

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
            {"login": "fresh", "uid": 1002},  # discovered → drift
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        body = resp.json()
        assert body["created"] == 1
        assert body["present"] == 1
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
        assert resp.status_code == 403

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
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

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
        в `test_w21_w1_os_whitelist.py` (там запись НЕ создаётся и эмитится
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
        assert resp.status_code == 422


@pytest.mark.usefixtures("soft_dept_mode")
class TestDiscoveredAccountNoPassword:
    async def test_reveal_on_discovered_returns_card_without_password(
        self, client, worker_bot_token_a, admin_role_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a),
            json={"users": [{"login": "ops", "uid": 1001}]},
        )
        await db.commit()
        acc = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "ops")
        )).scalar_one()
        # GET с view_password не должен падать — пароля нет, password_b64 = null.
        resp = await client.get(
            f"/api/server/v1/server-accounts/{acc.id}",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "discovered"
        assert body["password_b64"] is None

    async def test_internal_fetch_password_on_discovered_returns_empty(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a),
            json={"users": [{"login": "ops", "uid": 1001}]},
        )
        await db.commit()
        acc = (await db.execute(
            select(ServerAccount).where(ServerAccount.login == "ops")
        )).scalar_one()
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_bot_token_a),
        )
        # Discovered-аккаунт без сохранённого пароля — 200 с пустым password,
        # login отдаём как есть.
        assert resp.status_code == 200
        body = resp.json()
        assert body["login"] == "ops"
        assert body["password"] == ""
