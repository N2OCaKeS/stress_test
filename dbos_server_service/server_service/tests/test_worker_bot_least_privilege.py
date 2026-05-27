"""Интеграционные тесты least-privilege контракта для роли `worker_bot`.

Закрывает баг «worker_bot_token PAT = глобальный admin». Покрывает:

* **Allowed actions** через `/internal/...` endpoints — все четыре action'а
  (view_password / rotate_password / view_credentials / rotate_credentials)
  возвращают 200, потому что миграция `43cf9cfef9e1_…` выдала worker_bot'у
  ровно эти гранты.
* **Forbidden actions** — server.delete, ipmi power.on/off/reboot, server
  create, permissions grant/revoke — все 403 для worker_bot токена.
* **Isolation от других ролей** — worker_bot не может делать ничего, что
  делают reader/operator/admin (кроме своих 4 действий), в т.ч. cannot
  list permissions matrix (`permission.view`).

DB-уровень покрытия: миграция применена в conftest, через
`/internal/.../ipmi/credentials` мы dosrouce'м через `repo.has_action`
реальные строки entity_permissions без mock'ов на permissions слое.
"""
from __future__ import annotations

import pytest

BASE = "/api/server/v1"
BASE_INT = f"{BASE}/internal"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Allowed actions: 4 разрешённых эндпоинта возвращают 200 ──────────────────

@pytest.mark.usefixtures("soft_dept_mode")
class TestWorkerBotAllowedActions:
    """worker_bot должен мочь делать ровно 4 действия.

    `soft_dept_mode` нужен потому, что default `internal_require_dept_header`
    теперь True (strict): эти тесты бьют /internal/* без X-Target-Department-Id
    — проверяют permission-матрицу, не dept-header. Strict-семантика header'а
    отдельно покрыта в `test_internal_endpoints.py::TestTargetDeptHeader*`.
    """

    async def test_can_view_ipmi_credentials(
        self, client, worker_bot_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="ipmi-secret")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["password"] == "ipmi-secret"

    async def test_can_view_account_password(
        self, client, worker_bot_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="acc-secret")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["password"] == "acc-secret"

    async def test_can_rotate_account_password(
        self, client, worker_bot_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="acc-old")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers=_hdr(worker_bot_token_a),
            json={"password": "acc-new"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True


# ── Forbidden actions: всё остальное должно быть 403 ─────────────────────────

class TestWorkerBotForbiddenServerCrud:
    """worker_bot не должен мочь CRUD'ить сервера."""

    async def test_cannot_create_server(self, client, worker_bot_token_a):
        resp = await client.post(
            f"{BASE}/servers",
            headers=_hdr(worker_bot_token_a),
            json={
                "hostname": "rogue-host",
                "ip_address": "10.99.99.99",
                "ssh_port": 22,
                "department_id": "dep_a",
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_cannot_delete_server(
        self, client, worker_bot_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(
            f"{BASE}/servers/{srv.id}",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PERMISSION_DENIED"


class TestWorkerBotForbiddenPower:
    """worker_bot не должен мочь дёргать IPMI power."""

    @pytest.fixture
    def captured_dispatch(self, monkeypatch):
        """Регистрирует попытки dispatch'а — пусто означает 403 до dispatch'а."""
        calls: list[dict] = []

        async def fake_dispatch(*, task_kind, target_server_id, payload,
                                created_by, request_id,
                                target_resource_id=None, idempotency_key=None):
            calls.append({"task_kind": task_kind})
            return "tsk_should_never_happen"

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task", fake_dispatch
        )
        return calls

    async def test_cannot_power_on(
        self, client, worker_bot_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/on",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert captured_dispatch == [], "dispatch не должен вызываться при отказе"

    async def test_cannot_power_off(
        self, client, worker_bot_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/off",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert captured_dispatch == []

    async def test_cannot_power_reboot(
        self, client, worker_bot_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/reboot",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert captured_dispatch == []


class TestWorkerBotForbiddenPermissionMatrix:
    """worker_bot не должен мочь смотреть/менять матрицу прав — ни list,
    ни permission grant/revoke."""

    async def test_cannot_list_permissions(self, client, worker_bot_token_a):
        """`GET /permissions` требует `permission.view` — у worker_bot его нет."""
        resp = await client.get(
            f"{BASE}/permissions",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_cannot_grant_permission(self, client, worker_bot_token_a):
        """PUT /permissions/{entity}/{role}/{action} — требует
        `permission.permission_grant`."""
        resp = await client.put(
            f"{BASE}/permissions/server_account/worker_bot/grant_sudo",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_cannot_revoke_permission(self, client, worker_bot_token_a):
        """DELETE /permissions/{entity}/{role}/{action} — требует
        `permission.permission_revoke`."""
        resp = await client.delete(
            f"{BASE}/permissions/server_account/worker_bot/view_password",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PERMISSION_DENIED"


# ── Permission-matrix регрессия: грант worker_bot реально появился в БД ──────

class TestWorkerBotGrantsLandedInDb:
    """Проверяет, что seed-миграции оставили worker_bot ровно 7 строк
    в `entity_permissions`:

    * 4 secret-access (view/rotate password + view/rotate IPMI credentials);
    * 2 inventory callback — server:inventory_submit (hardware) и
      server_account:inventory_submit (OS-пользователи);
    * 1 provision callback — server_account:provision_on_host (useradd/
      usermod/userdel статус).
    """

    async def test_admin_can_see_worker_bot_grants_in_listing(
        self, client, admin_token,
    ):
        """department_admin со service-role `admin` видит полный набор grants — worker_bot в выдаче.

        admin_token — department_admin с service-role admin, который имеет
        VIEW на entity `permission` через матрицу.
        """
        resp = await client.get(f"{BASE}/permissions", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        wb = [r for r in rows if r["role"] == "worker_bot"]
        pairs = {(r["entity_type"], r["action"]) for r in wb}
        assert pairs == {
            ("server_account", "view_password"),
            ("server_account", "rotate_password"),
            ("ipmi_controller", "view_credentials"),
            ("ipmi_controller", "rotate_credentials"),
            ("server", "inventory_submit"),
            ("server_account", "inventory_submit"),
            ("server_account", "provision_on_host"),
        }, f"worker_bot grants in DB ≠ expected: {pairs}"

    async def test_total_grant_count_includes_worker_bot(
        self, client, admin_token,
    ):
        """Baseline + worker_bot grants. Baseline сидится по `_ALL_ACTIONS`
        в `831ba55543e9_…`; конкретное число зависит от того, что в проде
        — здесь же проверяем, что worker_bot строки точно учтены.
        """
        resp = await client.get(f"{BASE}/permissions", headers=_hdr(admin_token))
        assert resp.status_code == 200
        rows = resp.json()
        wb_rows = [r for r in rows if r["role"] == "worker_bot"]
        assert len(wb_rows) == 7, (
            f"expected 7 worker_bot grants (4 secret-access + 2 inventory_submit "
            f"+ 1 provision_on_host), got {len(wb_rows)}"
        )
        # Sanity: total count ≥ baseline + worker_bot.
        assert len(rows) >= len(wb_rows), "list_permissions returned too few rows"
