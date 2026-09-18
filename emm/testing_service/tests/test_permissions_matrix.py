"""Тесты `/api/testing/v1/permissions` — грант/ревок матрицы entity_permissions.

* GET / — список всех grants (требует VIEW на entity `permission`).
* GET /catalog — read-only справочник сущностей/действий.
* GET /{entity_type} — фильтр по entity (422 при unknown entity_type).
* PUT /{entity}/{role}/{action} — grant + валидация по ENTITY_ACTIONS
  whitelist, идемпотентность, системные роли (admin/guest) неизменяемы.
* DELETE /{entity}/{role}/{action} — revoke + 404 при несуществующем grant.

Авторизация: обычный department-bound caller нуждается в explicit-grant'е
`(permission, *, view/permission_grant/permission_revoke)` — по умолчанию их
несёт только сервисная роль `admin` (см. миграцию a7f4c9e2b816). Платформенные
`department_admin` (своего отдела) и `account_admin` (любого отдела) проходят
без ролевой проверки — соответствующий bypass в `permission_service.py`.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/permissions"


@pytest.fixture
def dept_b() -> str:
    return "dep_b"


@pytest.fixture
def admin_token_b(make_token, dept_b) -> str:
    return make_token(
        department_id=dept_b,
        service_roles={"testing_service": ["admin"]},
    )


@pytest.fixture
def account_admin_token(make_token) -> str:
    return make_token(platform_role="account_admin")


@pytest.fixture
def department_admin_token(make_token, dept_a) -> str:
    """department_admin своего отдела, БЕЗ сервисных ролей testing_service."""
    return make_token(platform_role="department_admin", department_id=dept_a)


# ── GET / ────────────────────────────────────────────────────────────────────

class TestListAll:
    async def test_admin_lists_default_grants(self, client, admin_token):
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["described"] is False
        rows = body["items"]
        assert body["total"] == len(rows)
        admin_grants = [r for r in rows if r["role"] == "admin"]
        # global_variable(4) + test_definition(4) + test_command_arg(3, mirrors
        # test_definition entity_type, не отдельная строка) + test_stand(5) +
        # department_test_settings(2) + test_run(1) + stp_test_case(4) +
        # stp_test_run(1) + stp_cell(1) + department_integration_settings(2) +
        # department_report_member(4) + department_activity_report(2) +
        # permission(3, эта миграция) — точное число хрупко, проверяем нижнюю
        # границу и наличие новой зоны permission.
        assert len(admin_grants) >= 3
        assert any(r["entity_type"] == "permission" for r in admin_grants)

    async def test_no_role_forbidden(self, client, no_role_token):
        resp = await client.get(BASE, headers=_hdr(no_role_token))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_guest_forbidden(self, client, guest_token):
        resp = await client.get(BASE, headers=_hdr(guest_token))
        assert resp.status_code == 403

    async def test_anonymous_401(self, client):
        resp = await client.get(BASE)
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "ACCESS_TOKEN_MISSING"

    async def test_role_filter(self, client, admin_token):
        resp = await client.get(f"{BASE}?role=admin", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"]
        assert all(r["role"] == "admin" for r in body["items"])

    async def test_role_filter_unknown_role_empty(self, client, admin_token):
        resp = await client.get(f"{BASE}?role=no_such_role", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_describe_adds_descriptions(self, client, admin_token):
        resp = await client.get(f"{BASE}?describe=true", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["described"] is True
        for r in body["items"]:
            assert "entity_description" in r
            assert "action_description" in r
            assert "sensitive" in r

    async def test_no_describe_keeps_legacy_shape(self, client, admin_token):
        resp = await client.get(BASE, headers=_hdr(admin_token))
        body = resp.json()
        row = body["items"][0]
        assert "entity_description" not in row
        assert "sensitive" not in row


# ── GET /catalog ─────────────────────────────────────────────────────────────

class TestCatalog:
    async def test_returns_all_entities(self, client, admin_token):
        from src.core.constants import EntityType

        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        got = {e["entity_type"] for e in resp.json()}
        assert got == {e.value for e in EntityType}

    async def test_actions_match_entity_actions(self, client, admin_token):
        from src.core.constants import ENTITY_ACTIONS

        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        for entity in resp.json():
            actions = {a["action"] for a in entity["actions"]}
            assert actions == set(ENTITY_ACTIONS[entity["entity_type"]])

    async def test_descriptions_nonempty(self, client, admin_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        for entity in resp.json():
            assert entity["description"].strip()
            for action in entity["actions"]:
                assert action["description"].strip()

    async def test_sensitive_flag(self, client, admin_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        flat = {
            (e["entity_type"], a["action"]): a
            for e in resp.json() for a in e["actions"]
        }
        assert flat[("test_stand", "view_test_credentials")]["sensitive"] is True
        assert flat[("test_stand", "view")]["sensitive"] is False
        assert flat[("permission", "permission_grant")]["sensitive"] is False

    async def test_guest_forbidden(self, client, guest_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(guest_token))
        assert resp.status_code == 403

    async def test_anonymous_401(self, client):
        resp = await client.get(f"{BASE}/catalog")
        assert resp.status_code == 401

    async def test_catalog_not_treated_as_entity_type(self, client, admin_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        assert resp.status_code == 200


# ── GET /{entity_type} ───────────────────────────────────────────────────────

class TestListForEntity:
    async def test_filters_by_entity(self, client, admin_token):
        resp = await client.get(f"{BASE}/permission", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["described"] is False
        rows = body["items"]
        assert body["total"] == len(rows)
        assert all(r["entity_type"] == "permission" for r in rows)
        admin_rows = [r for r in rows if r["role"] == "admin"]
        assert {r["action"] for r in admin_rows} == {"view", "permission_grant", "permission_revoke"}

    async def test_unknown_entity_type_422(self, client, admin_token):
        resp = await client.get(f"{BASE}/nonexistent_type", headers=_hdr(admin_token))
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "UNKNOWN_ENTITY_TYPE"


# ── PUT /{entity}/{role}/{action} ────────────────────────────────────────────

class TestGrant:
    async def test_admin_grants_new_combination(self, client, admin_token):
        resp = await client.put(
            f"{BASE}/test_stand/custom_role/view", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["entity_type"] == "test_stand"
        assert body["role"] == "custom_role"
        assert body["action"] == "view"
        assert body["department_id"] == "dep_a"

    async def test_unknown_action_for_entity_422(self, client, admin_token):
        resp = await client.put(
            f"{BASE}/test_stand/custom_role/UNKNOWN_ACT", headers=_hdr(admin_token),
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "INVALID_ACTION_FOR_ENTITY"

    async def test_grant_for_unknown_entity_422(self, client, admin_token):
        resp = await client.put(
            f"{BASE}/nonexistent_entity/custom_role/view", headers=_hdr(admin_token),
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "INVALID_ACTION_FOR_ENTITY"

    async def test_idempotent_grant_returns_existing(self, client, admin_token):
        first = await client.put(
            f"{BASE}/test_stand/idempotent_role/update", headers=_hdr(admin_token),
        )
        assert first.status_code == 200
        second = await client.put(
            f"{BASE}/test_stand/idempotent_role/update", headers=_hdr(admin_token),
        )
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

    async def test_system_role_immutable(self, client, admin_token):
        resp = await client.put(
            f"{BASE}/test_stand/admin/view", headers=_hdr(admin_token),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SYSTEM_ROLE_IMMUTABLE"

        resp_guest = await client.put(
            f"{BASE}/test_stand/guest/view", headers=_hdr(admin_token),
        )
        assert resp_guest.status_code == 409
        assert resp_guest.json()["error_code"] == "SYSTEM_ROLE_IMMUTABLE"

    async def test_no_role_cannot_grant(self, client, no_role_token):
        resp = await client.put(
            f"{BASE}/test_stand/custom_role/view", headers=_hdr(no_role_token),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_guest_cannot_grant(self, client, guest_token):
        resp = await client.put(
            f"{BASE}/test_stand/custom_role/view", headers=_hdr(guest_token),
        )
        assert resp.status_code == 403

    async def test_dept_isolation_on_target_department(
        self, client, admin_token, dept_b,
    ):
        resp = await client.put(
            f"{BASE}/test_stand/custom_role/view",
            headers=_hdr(admin_token),
            json={"target_department_id": dept_b},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"

    async def test_own_department_in_body_is_ok(self, client, admin_token, dept_a):
        resp = await client.put(
            f"{BASE}/test_stand/own_dept_role/view",
            headers=_hdr(admin_token),
            json={"target_department_id": dept_a},
        )
        assert resp.status_code == 200
        assert resp.json()["department_id"] == dept_a


# ── DELETE /{entity}/{role}/{action} ─────────────────────────────────────────

class TestRevoke:
    async def test_admin_grant_then_revoke(self, client, admin_token):
        put = await client.put(
            f"{BASE}/test_stand/revoke_role/update", headers=_hdr(admin_token),
        )
        assert put.status_code == 200
        resp = await client.delete(
            f"{BASE}/test_stand/revoke_role/update", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_revoke_nonexistent_returns_404(self, client, admin_token):
        resp = await client.delete(
            f"{BASE}/test_stand/no_such_role/delete", headers=_hdr(admin_token),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "PERMISSION_NOT_FOUND"

    async def test_revoke_twice_returns_404(self, client, admin_token):
        await client.put(f"{BASE}/test_stand/twice_role/update", headers=_hdr(admin_token))
        first = await client.delete(f"{BASE}/test_stand/twice_role/update", headers=_hdr(admin_token))
        assert first.status_code == 200
        second = await client.delete(f"{BASE}/test_stand/twice_role/update", headers=_hdr(admin_token))
        assert second.status_code == 404

    async def test_no_role_cannot_revoke(self, client, no_role_token):
        resp = await client.delete(
            f"{BASE}/test_stand/admin/view", headers=_hdr(no_role_token),
        )
        assert resp.status_code == 403

    async def test_system_role_immutable_on_revoke(self, client, admin_token):
        resp = await client.delete(
            f"{BASE}/global_variable/admin/view", headers=_hdr(admin_token),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SYSTEM_ROLE_IMMUTABLE"


# ── department_admin bypass (без сервисных ролей testing_service) ───────────

class TestDepartmentAdminBypass:
    """department_admin своего отдела управляет матрицей без service_roles."""

    async def test_lists_matrix(self, client, department_admin_token):
        resp = await client.get(BASE, headers=_hdr(department_admin_token))
        assert resp.status_code == 200, resp.text

    async def test_reads_catalog(self, client, department_admin_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(department_admin_token))
        assert resp.status_code == 200

    async def test_full_grant_revoke_cycle_in_own_department(
        self, client, department_admin_token, dept_a,
    ):
        grant = await client.put(
            f"{BASE}/test_stand/dept_admin_role/view", headers=_hdr(department_admin_token),
        )
        assert grant.status_code == 200, grant.text
        assert grant.json()["department_id"] == dept_a
        revoke = await client.delete(
            f"{BASE}/test_stand/dept_admin_role/view", headers=_hdr(department_admin_token),
        )
        assert revoke.status_code == 200

    async def test_cannot_grant_for_other_department(
        self, client, department_admin_token, dept_b,
    ):
        resp = await client.put(
            f"{BASE}/test_stand/dept_admin_role/view",
            headers=_hdr(department_admin_token),
            json={"target_department_id": dept_b},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"


# ── account_admin как мета-админ матрицы прав ────────────────────────────────

class TestAccountAdminMatrixMetaAdmin:
    async def test_lists_matrix(self, client, account_admin_token):
        resp = await client.get(BASE, headers=_hdr(account_admin_token))
        assert resp.status_code == 200, resp.text

    async def test_reads_catalog(self, client, account_admin_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(account_admin_token))
        assert resp.status_code == 200

    async def test_sees_all_departments(
        self, client, account_admin_token, admin_token, admin_token_b, dept_a, dept_b,
    ):
        a = await client.put(f"{BASE}/test_stand/role_a_only/view", headers=_hdr(admin_token))
        b = await client.put(f"{BASE}/test_stand/role_b_only/view", headers=_hdr(admin_token_b))
        assert a.status_code == 200 and b.status_code == 200
        resp = await client.get(BASE, headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.json()["items"]}
        assert a.json()["id"] in ids
        assert b.json()["id"] in ids

    async def test_grants_for_arbitrary_target_department(
        self, client, account_admin_token, dept_b,
    ):
        resp = await client.put(
            f"{BASE}/test_stand/account_admin_role/view",
            headers=_hdr(account_admin_token),
            json={"target_department_id": dept_b},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["department_id"] == dept_b

    async def test_revokes_for_arbitrary_target_department(
        self, client, account_admin_token, dept_a,
    ):
        put = await client.put(
            f"{BASE}/test_stand/account_admin_revoke_role/update",
            headers=_hdr(account_admin_token),
            json={"target_department_id": dept_a},
        )
        assert put.status_code == 200, put.text
        rev = await client.delete(
            f"{BASE}/test_stand/account_admin_revoke_role/update?target_department_id={dept_a}",
            headers=_hdr(account_admin_token),
        )
        assert rev.status_code == 200, rev.text

    async def test_invalid_action_422(self, client, account_admin_token, dept_a):
        resp = await client.put(
            f"{BASE}/test_stand/account_admin_role/UNKNOWN_ACT",
            headers=_hdr(account_admin_token),
            json={"target_department_id": dept_a},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "INVALID_ACTION_FOR_ENTITY"

    async def test_revoke_nonexistent_404(self, client, account_admin_token, dept_a):
        resp = await client.delete(
            f"{BASE}/test_stand/no_such_role/delete?target_department_id={dept_a}",
            headers=_hdr(account_admin_token),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "PERMISSION_NOT_FOUND"


# ── Department scope on read ─────────────────────────────────────────────────

class TestListScopeByDepartment:
    async def _seed_cross_dept_rows(self, client, admin_token, admin_token_b):
        a = await client.put(f"{BASE}/test_stand/dep_a_scope_role/view", headers=_hdr(admin_token))
        assert a.status_code == 200, a.text
        b = await client.put(f"{BASE}/test_stand/dep_b_scope_role/view", headers=_hdr(admin_token_b))
        assert b.status_code == 200, b.text
        return a.json(), b.json()

    async def test_dept_admin_sees_own_dept_and_system_wide_only(
        self, client, admin_token, admin_token_b, dept_a,
    ):
        row_a, row_b = await self._seed_cross_dept_rows(client, admin_token, admin_token_b)
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200
        rows = resp.json()["items"]
        dept_ids = {r["department_id"] for r in rows}
        assert dept_ids <= {None, dept_a}
        assert any(r["id"] == row_a["id"] for r in rows)
        assert not any(r["id"] == row_b["id"] for r in rows)

    async def test_dept_admin_cannot_see_other_dept_via_entity_filter(
        self, client, admin_token, admin_token_b, dept_a,
    ):
        _, row_b = await self._seed_cross_dept_rows(client, admin_token, admin_token_b)
        resp = await client.get(f"{BASE}/test_stand", headers=_hdr(admin_token))
        assert resp.status_code == 200
        rows = resp.json()["items"]
        ids = {r["id"] for r in rows}
        assert row_b["id"] not in ids
        for r in rows:
            assert r["department_id"] in (None, dept_a)


# ── Сид системной роли guest ─────────────────────────────────────────────────

class TestGuestBaselineSeed:
    """Роль `guest` присутствует в матрице, как в server_service/secret_service.

    До миграции `f4a9d2c61b38` матрица не несла о `guest` ни строки, хотя
    `constants.SYSTEM_SERVICE_ROLES` и `permission_service._reject_system_role`
    считают её системной с фиксированным набором «view открытых каталогов».
    """

    _EXPECTED = {
        ("global_variable", "view"),
        ("test_definition", "view"),
        ("test_stand", "view"),
        ("stp_test_case", "view"),
    }

    async def test_guest_rows_are_seeded_system_wide(self, client, admin_token):
        resp = await client.get(f"{BASE}?role=guest", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        rows = resp.json()["items"]
        assert rows, "у guest должна быть хотя бы одна строка матрицы"
        assert {(r["entity_type"], r["action"]) for r in rows} == self._EXPECTED
        assert all(r["department_id"] is None for r in rows)

    async def test_guest_gets_nothing_sensitive(self, client, admin_token):
        resp = await client.get(f"{BASE}?role=guest", headers=_hdr(admin_token))
        granted = {(r["entity_type"], r["action"]) for r in resp.json()["items"]}
        # Секрет учётки, вся матрица прав и HR-данные отдела — не для guest.
        assert ("test_stand", "view_test_credentials") not in granted
        assert not any(entity == "permission" for entity, _a in granted)
        assert not any(entity == "department_activity_report" for entity, _a in granted)
        assert all(action == "view" for _e, action in granted)

    async def test_guest_matrix_stays_immutable_via_api(self, client, admin_token):
        """Сид не открыл guest'у путь к правке — системная роль по-прежнему заперта."""
        resp = await client.put(
            f"{BASE}/test_stand/guest/update", headers=_hdr(admin_token),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SYSTEM_ROLE_IMMUTABLE"
