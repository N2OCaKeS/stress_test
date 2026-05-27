"""Тесты инвентаризации OS-пользователей.

Покрывает:
* `POST /servers/{id}/users/inventory` (user-trigger) — 202 + task_id,
  permissions/visibility, decommissioned;
* `POST /internal/servers/{id}/users/inventory` (worker callback) — reconcile:
  create discovered / update existing / mark drift; права (worker_bot может,
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

    async def fake_dispatch(*, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None):
        if idempotency_key is not None and idempotency_key in by_key:
            return by_key[idempotency_key]
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if idempotency_key is not None:
            by_key[idempotency_key] = new_id
        return new_id

    monkeypatch.setattr(
        "src.api.v1.endpoints.inventory.worker_client.dispatch_task", fake_dispatch,
    )
    return calls


@pytest.fixture
def captured_emits(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    for path in (
        "src.api.v1.endpoints.inventory.audit_service.emit",
        "src.services.internal_service.audit_service.emit",
    ):
        try:
            monkeypatch.setattr(path, fake_emit)
        except (AttributeError, ImportError):
            pass
    return captured


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
        ok = [e for e in _events(captured_emits, "server_account.users_inventory")
              if e["status"] == "success"]
        assert len(ok) == 1
        assert ok[0]["details"]["task_kind"] == "users.inventory"


# ── Callback reconcile: POST /internal/servers/{id}/users/inventory ──────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestUsersInventoryReconcile:
    async def test_creates_discovered_account(
        self, client, worker_bot_token_a, make_server, db, dept_a,
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
        assert body["updated"] == 0
        assert body["drifted"] == 0

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

    async def test_updates_existing_metadata(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="postgres", has_sudo=False)
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
        assert resp.json()["updated"] == 1
        assert resp.json()["created"] == 0

        await db.commit()
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.has_sudo is True
        assert refreshed.shell == "/bin/sh"
        assert refreshed.home_dir == "/var/lib/postgresql"
        # managed-аккаунт не теряет пароль при обновлении метаданных.
        assert refreshed.password_encrypted is not None
        assert refreshed.source == "managed"
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

    async def test_mixed_create_update_drift(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        await make_account(server_id=srv.id, login="keep")
        await make_account(server_id=srv.id, login="gone")
        payload = {"users": [
            {"login": "keep", "uid": 1001},   # update
            {"login": "fresh", "uid": 1002},  # create
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json=payload,
        )
        body = resp.json()
        assert body["created"] == 1
        assert body["updated"] == 1
        assert body["drifted"] == 1

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

    async def test_internal_fetch_password_on_discovered_404(
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
        # Понятная ошибка, не краш.
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ACCOUNT_HAS_NO_PASSWORD"
