"""Кластер C: эндпоинты /permissions* (catalog / list / put / delete).

Проверки:

* `GET /permissions/catalog` — operator/reader/admin отдают 200 со
  стабильным набором полей (entity_type/description/actions с
  `sensitive`/`worker_only`); guest → 403; без токена → 401.
* `GET /permissions` — фильтр `?role=`, флаг `?describe=true`,
  cross-dept-строки не возвращаются обычному dept-admin'у в `?role=`.
* `PUT /permissions/{entity}/{role}/{action}` — own-dept ok,
  cross-dept → 403 DEPARTMENT_ISOLATION, permission_grant-gate.
* `DELETE /permissions/{entity}/{role}/{action}` — 404 на чужом dept,
  permission_revoke-gate.

Кастомная роль end-to-end лежит в `test_e2e_C_permissions_sweep.py`.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration._helpers_C_permissions import (
    SERVICE_NAME,
    auth_header,
    ensure_role,
    find_event,
    now_utc,
    setup_cluster_c,
)


# ── Module-scoped setup ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def cluster_c(integration_stack, auth_client: httpx.Client, admin_token: str):
    return setup_cluster_c(auth_client, admin_token)


# ── /permissions/catalog ─────────────────────────────────────────────────────


class TestPermissionsCatalog:
    def test_admin_sees_catalog(self, cluster_c, server_client: httpx.Client):
        r = server_client.get(
            "/api/server/v1/permissions/catalog",
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        entity_types = {e["entity_type"] for e in body}
        assert {"server", "server_account", "os_version", "ipmi_controller", "permission"} <= entity_types
        # Каждая запись несёт описание и список actions с флагами.
        for entry in body:
            assert "description" in entry
            for a in entry["actions"]:
                assert {"action", "description", "sensitive", "worker_only"} <= a.keys()

    def test_operator_sees_catalog(self, cluster_c, server_client: httpx.Client):
        r = server_client.get(
            "/api/server/v1/permissions/catalog",
            headers=auth_header(cluster_c.operator_token),
        )
        assert r.status_code == 200, r.text

    def test_reader_sees_catalog(self, cluster_c, server_client: httpx.Client):
        r = server_client.get(
            "/api/server/v1/permissions/catalog",
            headers=auth_header(cluster_c.reader_token),
        )
        assert r.status_code == 200, r.text

    def test_guest_blocked(self, cluster_c, server_client: httpx.Client):
        r = server_client.get(
            "/api/server/v1/permissions/catalog",
            headers=auth_header(cluster_c.guest_token),
        )
        assert r.status_code == 403, r.text

    def test_anonymous_blocked(self, cluster_c, server_client: httpx.Client):
        r = server_client.get("/api/server/v1/permissions/catalog")
        assert r.status_code == 401, r.text

    def test_sensitive_flag_set_for_power_on(
        self, cluster_c, server_client: httpx.Client
    ):
        r = server_client.get(
            "/api/server/v1/permissions/catalog",
            headers=auth_header(cluster_c.admin_token),
        )
        server_entry = next(e for e in r.json() if e["entity_type"] == "server")
        actions = {a["action"]: a for a in server_entry["actions"]}
        assert actions["power_on"]["sensitive"] is True
        assert actions["view"]["sensitive"] is False

    def test_worker_only_flag_set_for_inventory_submit(
        self, cluster_c, server_client: httpx.Client
    ):
        r = server_client.get(
            "/api/server/v1/permissions/catalog",
            headers=auth_header(cluster_c.admin_token),
        )
        server_entry = next(e for e in r.json() if e["entity_type"] == "server")
        actions = {a["action"]: a for a in server_entry["actions"]}
        assert actions["inventory_submit"]["worker_only"] is True
        assert actions["view"]["worker_only"] is False


# ── GET /permissions ─────────────────────────────────────────────────────────


class TestPermissionsList:
    def test_list_all_admin(self, cluster_c, server_client: httpx.Client):
        r = server_client.get(
            "/api/server/v1/permissions",
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)
        # seed-grant'ы для admin/reader/operator должны там быть.
        roles = {row["role"] for row in body}
        assert {"admin", "reader", "operator"} <= roles

    def test_list_filter_by_role(self, cluster_c, server_client: httpx.Client):
        r = server_client.get(
            "/api/server/v1/permissions",
            params={"role": "reader"},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Только reader-строки.
        for row in body:
            assert row["role"] == "reader"

    def test_describe_flag(self, cluster_c, server_client: httpx.Client):
        r = server_client.get(
            "/api/server/v1/permissions",
            params={"describe": "true", "role": "reader"},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body, "expected at least one reader row"
        # describe=true → каждая строка содержит entity_description/action_description/sensitive.
        for row in body:
            assert "entity_description" in row
            assert "action_description" in row
            assert "sensitive" in row

    def test_guest_blocked(self, cluster_c, server_client: httpx.Client):
        r = server_client.get(
            "/api/server/v1/permissions",
            headers=auth_header(cluster_c.guest_token),
        )
        assert r.status_code == 403, r.text

    def test_anonymous_blocked(self, cluster_c, server_client: httpx.Client):
        r = server_client.get("/api/server/v1/permissions")
        assert r.status_code == 401, r.text

    def test_cross_dept_custom_role_not_visible(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
    ):
        """Кастомная роль другого dept'а не должна светиться в нашей выдаче `?role=`.

        Заводим роль `other_only` в `cluster_c.other_dept_id`, грантуем ей
        view на server. В нашем dept'е (admin) запрос `?role=other_only`
        должен вернуть пустой список — system-wide строк нет, а per-dept
        строка чужого отдела отбивается dept-scope'ом в repo.
        """
        role = "other_only"
        ensure_role(
            auth_client, admin_token, cluster_c.other_dept_id, SERVICE_NAME, role
        )
        # Грант в other_dept от его собственного admin'а.
        r = server_client.put(
            f"/api/server/v1/permissions/server/{role}/view",
            json={"target_department_id": cluster_c.other_dept_id},
            headers=auth_header(cluster_c.other_admin_token),
        )
        assert r.status_code in (200, 201), r.text

        # Наш admin спрашивает `?role=other_only` — пусто.
        r = server_client.get(
            "/api/server/v1/permissions",
            params={"role": role},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 200, r.text
        assert r.json() == [], f"cross-dept custom role leaked: {r.json()}"


# ── PUT /permissions/{entity}/{role}/{action} ────────────────────────────────


class TestPermissionsPut:
    def test_dept_admin_grant_own_dept_ok(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        role = "put_own_role"
        ensure_role(
            auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role
        )
        since = now_utc()
        r = server_client.put(
            f"/api/server/v1/permissions/server/{role}/view",
            json={"target_department_id": cluster_c.dept_id},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["role"] == role
        assert body["entity_type"] == "server"
        assert body["action"] == "view"
        ev = find_event(
            logging_client, action="permission.grant", status="success",
            from_time=since,
            extra_match=lambda it: (it.get("details") or {}).get("role") == role,
        )
        assert ev is not None, "permission.grant success audit not found"

    def test_grant_idempotent(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
    ):
        role = "put_idemp_role"
        ensure_role(
            auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role
        )
        # Дважды грантим ту же пару → второй раз noop, тот же id.
        url = f"/api/server/v1/permissions/server/{role}/view"
        body = {"target_department_id": cluster_c.dept_id}
        r1 = server_client.put(url, json=body, headers=auth_header(cluster_c.admin_token))
        r2 = server_client.put(url, json=body, headers=auth_header(cluster_c.admin_token))
        assert r1.status_code in (200, 201)
        assert r2.status_code in (200, 201)
        assert r1.json()["id"] == r2.json()["id"]

    def test_cross_dept_grant_denied(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        role = "cross_dept_target"
        ensure_role(
            auth_client, admin_token, cluster_c.other_dept_id, SERVICE_NAME, role
        )
        since = now_utc()
        r = server_client.put(
            f"/api/server/v1/permissions/server/{role}/view",
            json={"target_department_id": cluster_c.other_dept_id},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 403, r.text
        body = r.json()
        assert body.get("error_code") == "DEPARTMENT_ISOLATION", body
        ev = find_event(
            logging_client, action="permission.grant", status="denied",
            from_time=since,
            extra_match=lambda it: (it.get("details") or {}).get("reason") == "department_isolation_grant",
        )
        assert ev is not None, "permission.grant denied (isolation) audit not found"

    def test_permission_grant_gate(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        """Юзер с ролью без `permission_grant` (operator) не может выдать."""
        role = "perm_grant_gate"
        ensure_role(
            auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role
        )
        since = now_utc()
        r = server_client.put(
            f"/api/server/v1/permissions/server/{role}/view",
            json={"target_department_id": cluster_c.dept_id},
            headers=auth_header(cluster_c.operator_token),
        )
        assert r.status_code == 403, r.text
        ev = find_event(
            logging_client, action="permission.grant", status="denied",
            from_time=since,
            extra_match=lambda it: (it.get("details") or {}).get("reason") == "permission_denied",
        )
        assert ev is not None, "permission.grant denied (perm_denied) audit not found"

    def test_invalid_action_for_entity(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
    ):
        """`(os_version, power_on)` — невалидная пара, 422 INVALID_ACTION_FOR_ENTITY."""
        role = "invalid_action_role"
        ensure_role(
            auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role
        )
        r = server_client.put(
            f"/api/server/v1/permissions/os_version/{role}/power_on",
            json={"target_department_id": cluster_c.dept_id},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 422, r.text
        assert r.json().get("error_code") == "INVALID_ACTION_FOR_ENTITY"


# ── DELETE /permissions/{entity}/{role}/{action} ─────────────────────────────


class TestPermissionsDelete:
    def test_revoke_own_dept_ok(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        role = "del_own_role"
        ensure_role(
            auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role
        )
        # Сначала grant, потом revoke.
        server_client.put(
            f"/api/server/v1/permissions/server/{role}/view",
            json={"target_department_id": cluster_c.dept_id},
            headers=auth_header(cluster_c.admin_token),
        )
        since = now_utc()
        r = server_client.delete(
            f"/api/server/v1/permissions/server/{role}/view",
            params={"target_department_id": cluster_c.dept_id},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 200, r.text
        ev = find_event(
            logging_client, action="permission.revoke", status="success",
            from_time=since,
            extra_match=lambda it: (it.get("details") or {}).get("role") == role,
        )
        assert ev is not None, "permission.revoke success audit not found"

    def test_revoke_missing_returns_404(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
    ):
        role = "del_missing_role"
        ensure_role(
            auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role
        )
        r = server_client.delete(
            f"/api/server/v1/permissions/server/{role}/view",
            params={"target_department_id": cluster_c.dept_id},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 404, r.text
        assert r.json().get("error_code") == "PERMISSION_NOT_FOUND"

    def test_revoke_cross_dept_denied(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        role = "del_cross_dept_role"
        ensure_role(
            auth_client, admin_token, cluster_c.other_dept_id, SERVICE_NAME, role
        )
        # Зальём строку в чужом dept'е руками от их admin'а.
        server_client.put(
            f"/api/server/v1/permissions/server/{role}/view",
            json={"target_department_id": cluster_c.other_dept_id},
            headers=auth_header(cluster_c.other_admin_token),
        )
        since = now_utc()
        r = server_client.delete(
            f"/api/server/v1/permissions/server/{role}/view",
            params={"target_department_id": cluster_c.other_dept_id},
            headers=auth_header(cluster_c.admin_token),
        )
        assert r.status_code == 403, r.text
        assert r.json().get("error_code") == "DEPARTMENT_ISOLATION"
        ev = find_event(
            logging_client, action="permission.revoke", status="denied",
            from_time=since,
            extra_match=lambda it: (it.get("details") or {}).get("reason") == "department_isolation_revoke",
        )
        assert ev is not None, "permission.revoke denied (isolation) audit not found"

    def test_permission_revoke_gate(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        """Operator без `permission_revoke` не может отозвать строку."""
        role = "del_revoke_gate"
        ensure_role(
            auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role
        )
        server_client.put(
            f"/api/server/v1/permissions/server/{role}/view",
            json={"target_department_id": cluster_c.dept_id},
            headers=auth_header(cluster_c.admin_token),
        )
        since = now_utc()
        r = server_client.delete(
            f"/api/server/v1/permissions/server/{role}/view",
            params={"target_department_id": cluster_c.dept_id},
            headers=auth_header(cluster_c.operator_token),
        )
        assert r.status_code == 403, r.text
        ev = find_event(
            logging_client, action="permission.revoke", status="denied",
            from_time=since,
            extra_match=lambda it: (it.get("details") or {}).get("reason") == "permission_denied",
        )
        assert ev is not None, "permission.revoke denied (perm_denied) audit not found"
