"""Интеграционные тесты department-scope для `entity_permissions`.

Закрывает threat model: кастомная роль, созданная в департаменте A,
НЕ должна делиться grants'ами
с одноимённой кастомной ролью в департаменте B. Встроенные роли
(``admin``/``reader``/``operator``/``worker_bot``) остаются system-wide
(``department_id IS NULL``) и общими для всех департаментов.

All tests hit real PostgreSQL through the standard ``client``/``db``
fixtures from ``tests/conftest.py``; no DB mocks.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.models import EntityPermission
from src.repositories import entity_permission as repo

BASE = "/api/server/v1/permissions"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


@pytest_asyncio.fixture
async def dept_admin_token_a(make_token, dept_a):
    """`server_service.admin` в dept_a (custom service-role admin, не platform)."""
    return make_token(
        department_id=dept_a,
        service_roles={"server_service": ["admin"]},
    )


@pytest_asyncio.fixture
async def dept_admin_token_b(make_token, dept_b):
    """`server_service.admin` в dept_b."""
    return make_token(
        department_id=dept_b,
        service_roles={"server_service": ["admin"]},
    )


# ── 1. Custom-role grant scoped to actor's dept ──────────────────────────────

class TestCustomRoleGrantScopedToActorDept:
    async def test_custom_role_grant_scoped_to_actor_dept(
        self, client, db, dept_admin_token_a, dept_a,
    ):
        """dept_A admin grants `my_custom_role` → row stored with department_id=dept_A."""
        resp = await client.put(
            f"{BASE}/server/my_custom_role/delete",
            headers=_hdr(dept_admin_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["department_id"] == dept_a
        assert body["role"] == "my_custom_role"
        assert body["entity_type"] == "server"
        assert body["action"] == "delete"

        # Verify via DB
        rows = (await db.execute(
            select(EntityPermission).where(
                EntityPermission.role == "my_custom_role",
                EntityPermission.action == "delete",
                EntityPermission.entity_type == "server",
            )
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].department_id == dept_a


# ── 2. Cross-dept isolation: same role name → no leak ───────────────────────

class TestCrossDeptIsolation:
    async def test_dept_b_user_with_same_role_name_does_not_get_dept_a_grant(
        self,
        client, db,
        dept_admin_token_a, dept_a, dept_b,
    ):
        """The exploit scenario: dept_A admin grants (server, delete) to
        ``my_custom_role``. A dept_B user happens to also carry the
        ``my_custom_role`` service-role (independent custom role in dept_B)
        — they MUST NOT inherit the dept_A grant.
        """
        # Step 1 — dept_A admin creates the dept-scoped grant.
        grant = await client.put(
            f"{BASE}/server/my_custom_role/delete",
            headers=_hdr(dept_admin_token_a),
        )
        assert grant.status_code == 200
        assert grant.json()["department_id"] == dept_a

        # Step 2 — a dept_B user carrying the same role-name MUST NOT inherit
        #          the dept_A grant. Checked at the repo level (has_action),
        #          the lowest layer with no endpoint indirection.
        leak = await repo.has_action(
            db, "server", ["my_custom_role"], "delete",
            department_id=dept_b,
        )
        assert leak is False, (
            "Cross-dept privilege leak: dept_B user inherited dept_A's grant "
            "for the same custom role name"
        )

        # And the dept_A user IS allowed (positive symmetry).
        ok = await repo.has_action(
            db, "server", ["my_custom_role"], "delete",
            department_id=dept_a,
        )
        assert ok is True


# ── 3. Built-in roles remain global (regression) ────────────────────────────

class TestBuiltinRolesGlobal:
    async def test_built_in_role_remains_global_admin(
        self, db, dept_b,
    ):
        """admin/reader/operator are system-wide rows (department_id IS NULL).

        Even a dept_B user with one of those roles should see all the
        baseline grants from the seed migration — those rows were created
        with department_id=NULL and stay visible cross-department.
        """
        # `admin` for `server.delete` was seeded in migration 831ba55543e9.
        ok = await repo.has_action(
            db, "server", ["admin"], "delete", department_id=dept_b,
        )
        assert ok is True

    async def test_built_in_role_remains_global_reader(self, db, dept_b):
        ok = await repo.has_action(
            db, "server", ["reader"], "view", department_id=dept_b,
        )
        assert ok is True

    async def test_built_in_role_remains_global_operator(self, db, dept_b):
        ok = await repo.has_action(
            db, "server", ["operator"], "power_on", department_id=dept_b,
        )
        assert ok is True

    async def test_built_in_row_has_null_department_id(self, db):
        """Defence-in-depth: verify seeded built-in rows did not pick up a
        department_id by accident.
        """
        rows = (await db.execute(
            select(EntityPermission).where(
                EntityPermission.role.in_(
                    ["admin", "reader", "operator", "worker_bot", "guest"]
                ),
            )
        )).scalars().all()
        offenders = [r for r in rows if r.department_id is not None]
        assert offenders == [], (
            f"built-in roles must remain system-wide; got per-dept rows: "
            f"{[(r.role, r.action, r.entity_type, r.department_id) for r in offenders]}"
        )


# ── 4. Dept admin cannot grant to a different dept ──────────────────────────

class TestDeptAdminCannotGrantCrossDept:
    async def test_dept_admin_cannot_grant_to_other_dept(
        self, client, dept_admin_token_a, dept_b,
    ):
        """Passing target_department_id != actor's dept → 403 DEPARTMENT_ISOLATION."""
        resp = await client.put(
            f"{BASE}/server/my_role/delete",
            headers=_hdr(dept_admin_token_a),
            json={"target_department_id": dept_b},
        )
        assert_error(resp, 403, "DEPARTMENT_ISOLATION")

    async def test_dept_admin_cannot_grant_system_wide(
        self, client, dept_admin_token_a, dept_a,
    ):
        """A non-account_admin cannot mint a system-wide row (department_id=None)."""
        # The endpoint default for target_department_id is None — but a
        # non-account_admin is silently coerced to their own dept. Verify
        # the row landed in their dept, NOT system-wide.
        resp = await client.put(
            f"{BASE}/server/my_role/delete",
            headers=_hdr(dept_admin_token_a),
            # no body → target_department_id stays None at the boundary,
            # but _resolve_target_department_id rewrites it to actor's dept.
        )
        assert resp.status_code == 200
        assert resp.json()["department_id"] == dept_a, (
            "non-account_admin write must be scoped to actor's department, "
            "never NULL"
        )


# ── 5. account_admin — мета-админ матрицы прав ──────────────────────────────

class TestAccountAdminManagesPermissionsApi:
    """``account_admin`` управляет матрицей прав любого отдела (мета-админ).

    По решению владельца платформенный ``account_admin`` управляет правами
    отделов: ``platform_admin_guard`` пропускает его на ``/permissions*``,
    endpoint резолвит через ``PermissionMatrixIdentity``, а
    ``permission_service`` снимает ролевую проверку и dept-isolation. Сами
    серверные/аккаунтные операции ему по-прежнему недоступны (см.
    ``tests/integration/test_platform_admin_block.py``).
    """

    async def test_account_admin_grants_per_dept(
        self, client, db, account_admin_token, dept_a,
    ):
        """account_admin с явным target_department_id пишет grant в этот отдел."""
        resp = await client.put(
            f"{BASE}/server/per_dept_role/delete",
            headers=_hdr(account_admin_token),
            json={"target_department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["department_id"] == dept_a
        rows = (await db.execute(
            select(EntityPermission).where(
                EntityPermission.role == "per_dept_role",
            )
        )).scalars().all()
        assert [r.department_id for r in rows] == [dept_a]

    async def test_account_admin_grants_global(
        self, client, db, account_admin_token,
    ):
        """Без target_department_id grant ложится system-wide (department_id=NULL).

        account_admin — платформенный мета-админ: ему доступно управление и
        встроенными system-wide строками, не только per-dept.
        """
        resp = await client.put(
            f"{BASE}/server/some_global_role/delete",
            headers=_hdr(account_admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["department_id"] is None
        rows = (await db.execute(
            select(EntityPermission).where(
                EntityPermission.role == "some_global_role",
            )
        )).scalars().all()
        assert [r.department_id for r in rows] == [None]


# ── 7. Worker_bot seed preserved as system-wide (regression) ────────────────

class TestWorkerBotGrantsGlobalSeedPreserved:
    async def test_worker_bot_grants_global_seed_preserved(self, db):
        """After the dept-scope migration, the worker_bot grants stay
        system-wide (department_id IS NULL) so worker_bot keeps least-privilege
        contract regardless of which department the bot belongs to.

        Set: 4 initial grants (view/rotate credentials, view/rotate password)
        plus 2 callback grants (server:inventory_submit для hardware-инвентаризации
        и server_account:inventory_submit для инвентаризации OS-пользователей)
        плюс server_account:provision_on_host для статус-callback'а useradd/usermod/userdel
        плюс server:prepare_callback для callback'а бутстрапа управления
        плюс server:view_management_credentials для fetch'а per-server управляющих кред.
        """
        rows = (await db.execute(
            select(EntityPermission).where(EntityPermission.role == "worker_bot")
        )).scalars().all()
        assert len(rows) == 9
        for r in rows:
            assert r.department_id is None, (
                f"worker_bot grant {(r.entity_type, r.action)} must be system-wide"
            )

        expected = {
            ("server_account", "view_password"),
            ("server_account", "rotate_password"),
            ("ipmi_controller", "view_credentials"),
            ("ipmi_controller", "rotate_credentials"),
            ("server", "inventory_submit"),
            ("server_account", "inventory_submit"),
            ("server_account", "provision_on_host"),
            ("server", "prepare_callback"),
            ("server", "view_management_credentials"),
        }
        got = {(r.entity_type, r.action) for r in rows}
        assert got == expected


# ── 8. Audit: denied grant emits department_isolation_grant ────────────────

class TestDeptIsolationAuditEmitted:
    """Confirms that the 403 DEPARTMENT_ISOLATION grant attempt also emits a
    `permission.grant denied` audit event with the correct reason. Symmetric
    with the existing `reason=permission_denied` denial.
    """

    @pytest.fixture
    def captured_emits(self, monkeypatch):
        events: list[dict] = []
        from src.services import audit_service

        original_emit = audit_service.emit

        def capturing_emit(action: str, **kwargs):
            events.append({"action": action, **kwargs})
            return original_emit(action, **kwargs)

        monkeypatch.setattr(audit_service, "emit", capturing_emit)
        return events

    async def test_denied_grant_emits_department_isolation_reason(
        self, client, dept_admin_token_a, dept_b, captured_emits,
    ):
        resp = await client.put(
            f"{BASE}/server/x_role/delete",
            headers=_hdr(dept_admin_token_a),
            json={"target_department_id": dept_b},
        )
        assert_error(resp, 403, "DEPARTMENT_ISOLATION")

        denied = [
            e for e in captured_emits
            if e["action"] == "permission.grant"
            and e.get("status") == "denied"
        ]
        assert denied, "expected a permission.grant denied audit event"
        reasons = [
            d.get("details", {}).get("reason")
            for d in denied
        ]
        assert "department_isolation_grant" in reasons, (
            f"expected reason=department_isolation_grant, got {reasons}"
        )

    async def test_denied_revoke_emits_department_isolation_reason(
        self, client, dept_admin_token_a, dept_b, captured_emits,
    ):
        resp = await client.delete(
            f"{BASE}/server/x_role/delete",
            headers=_hdr(dept_admin_token_a),
            params={"target_department_id": dept_b},
        )
        assert_error(resp, 403, "DEPARTMENT_ISOLATION")
        denied = [
            e for e in captured_emits
            if e["action"] == "permission.revoke"
            and e.get("status") == "denied"
        ]
        assert denied
        reasons = [d.get("details", {}).get("reason") for d in denied]
        assert "department_isolation_revoke" in reasons, (
            f"expected reason=department_isolation_revoke, got {reasons}"
        )
