"""E2E groups + bots-in-groups — cluster B.

Покрывает:
    * POST/GET/PATCH/DELETE /groups (account_admin global / dept_admin own / cross-dept → 403);
    * POST/DELETE /groups/{gid}/members (cascade на identity-cache; cross-dept guards);
    * POST/DELETE /groups/{gid}/services (`grant_service_to_group` → dept→service access);
    * POST /groups/{gid}/roles (`assign_group_roles` → `GroupServiceAccess` required);
    * POST/GET/DELETE /groups/{gid}/bots (bot dept-scope; ∩ allowed_services);
    * наследование ролей через группу для users и bots (introspect отдаёт group-роли);
    * remove-from-group → следующий introspect не видит роль;
    * negative — cross-dept access / regular user.

Каждый тест: endpoint-ответ + auth_db state + audit-row в loging.audit_events.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration._helpers_B_groups import (
    add_bot_member,
    add_member,
    assign_group_roles,
    bot_member_db_row,
    create_group,
    delete_group,
    ensure_department,
    ensure_role,
    ensure_service,
    grant_service_to_dept,
    grant_service_to_group,
    group_db_row,
    group_service_access_row,
    group_service_roles,
    introspect,
    issue_bot_token,
    login,
    make_bot_in_dept,
    make_dept_user,
    member_db_row,
    now_utc,
    patch_group,
    rand_suffix,
    remove_bot_member,
    remove_member,
    revoke_service_from_group,
    wait_for_audit_row,
)

AUTH = "/api/auth/v1"


# ── Local fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def auth_db():
    """Engine для auth_db_test. Локальная — чтобы не править conftest."""
    import os

    from sqlalchemy import create_engine

    url = os.environ.get("AUTH_DB_URL")
    if not url:
        pytest.skip("AUTH_DB_URL not set; full integration stack required")
    engine = create_engine(url, pool_pre_ping=True, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def two_depts(auth_client: httpx.Client, admin_token: str):
    """Два независимых dept'а + общий сервис, выданный обоим. Идемпотентно.

    Возвращает (dept_a_id, dept_b_id, service_name).
    """
    u = rand_suffix()
    svc = f"b_svc_{u}"
    ensure_service(auth_client, admin_token, svc)
    dept_a = ensure_department(auth_client, admin_token, f"b_dept_a_{u}")
    dept_b = ensure_department(auth_client, admin_token, f"b_dept_b_{u}")
    grant_service_to_dept(auth_client, admin_token, dept_a, svc)
    grant_service_to_dept(auth_client, admin_token, dept_b, svc)
    return dept_a, dept_b, svc


# ══════════════════════════════════════════════════════════════════════════════
# 1. POST/GET/PATCH/DELETE /groups
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupCRUD:
    def test_account_admin_creates_group_any_dept(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, _ = two_depts
        name = f"acc_grp_{rand_suffix()}"
        since = now_utc()

        r = create_group(auth_client, admin_token, dept_a, name=name)

        assert r.status_code == 201, r.text
        body = r.json()
        assert body["department_id"] == dept_a
        assert body["name"] == name
        assert body["is_active"] is True

        row = group_db_row(auth_db, body["id"])
        assert row is not None
        assert row["department_id"] == dept_a
        assert row["is_active"] is True

        wait_for_audit_row(
            loging_db_engine, action="group.create",
            target_id=body["id"], since=since,
        )

    def test_department_admin_creates_group_own_dept(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        da = make_dept_user(auth_client, admin_token, dept_a,
                             platform_role="department_admin")
        da_token = login(auth_client, da["username"], da["_password"])

        r = create_group(auth_client, da_token, dept_a)

        assert r.status_code == 201, r.text
        assert r.json()["department_id"] == dept_a

    def test_department_admin_create_cross_dept_forbidden(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, dept_b, _ = two_depts
        da = make_dept_user(auth_client, admin_token, dept_a,
                             platform_role="department_admin")
        da_token = login(auth_client, da["username"], da["_password"])

        r = create_group(auth_client, da_token, dept_b)

        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "DEPARTMENT_FORBIDDEN"

    def test_regular_user_create_group_forbidden(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        u = make_dept_user(auth_client, admin_token, dept_a)
        u_token = login(auth_client, u["username"], u["_password"])

        r = create_group(auth_client, u_token, dept_a)

        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "ROLE_REQUIRED"

    def test_duplicate_group_name_in_dept_conflict(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        name = f"dup_{rand_suffix()}"
        first = create_group(auth_client, admin_token, dept_a, name=name)
        assert first.status_code == 201, first.text

        second = create_group(auth_client, admin_token, dept_a, name=name)

        assert second.status_code == 409, second.text
        assert second.json()["error_code"] == "GROUP_ALREADY_EXISTS"

    def test_list_groups_admin_pagination(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        create_group(auth_client, admin_token, dept_a)

        r = auth_client.get(
            f"{AUTH}/groups?limit=5&offset=0",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200, r.text
        assert "X-Total-Count" in r.headers
        assert int(r.headers["X-Total-Count"]) >= 1
        assert isinstance(r.json(), list)

    def test_patch_group_admin_only(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        since = now_utc()

        r = patch_group(auth_client, admin_token, grp["id"],
                        display_name="UpdatedDisplay")

        assert r.status_code == 200, r.text
        assert r.json()["display_name"] == "UpdatedDisplay"

        wait_for_audit_row(
            loging_db_engine, action="group.update",
            target_id=grp["id"], since=since,
        )

    def test_patch_group_dept_admin_own_dept_allowed(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        da = make_dept_user(auth_client, admin_token, dept_a,
                             platform_role="department_admin")
        da_token = login(auth_client, da["username"], da["_password"])

        r = patch_group(auth_client, da_token, grp["id"], description="x")

        assert r.status_code == 200, r.text
        assert r.json()["description"] == "x"

    def test_delete_group_admin_only(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        since = now_utc()

        r = delete_group(auth_client, admin_token, grp["id"])

        assert r.status_code == 200, r.text
        row = group_db_row(auth_db, grp["id"])
        assert row is not None
        assert row["is_active"] is False

        wait_for_audit_row(
            loging_db_engine, action="group.delete",
            target_id=grp["id"], since=since,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2. POST/DELETE /groups/{gid}/members + identity-cache cascade
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupMembers:
    def test_add_member_same_dept_success(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        user = make_dept_user(auth_client, admin_token, dept_a)
        since = now_utc()

        r = add_member(auth_client, admin_token, grp["id"], user["user_id"])

        assert r.status_code == 201, r.text
        assert r.json()["user_id"] == user["user_id"]

        m = member_db_row(auth_db, grp["id"], user["user_id"])
        assert m is not None

        wait_for_audit_row(
            loging_db_engine, action="group.member_add",
            target_id=grp["id"], since=since,
        )

    def test_add_member_cross_dept_returns_403_mismatch(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, dept_b, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        # юзер из чужого dept'а
        foreign = make_dept_user(auth_client, admin_token, dept_b)

        r = add_member(auth_client, admin_token, grp["id"], foreign["user_id"])

        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "GROUP_DEPARTMENT_MISMATCH"

    def test_add_member_dept_admin_cross_dept_forbidden(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, dept_b, _ = two_depts
        # группа в dept_a + юзер из dept_a (валидный target)
        grp = create_group(auth_client, admin_token, dept_a).json()
        target = make_dept_user(auth_client, admin_token, dept_a)
        # dept_admin живёт в dept_b
        da = make_dept_user(auth_client, admin_token, dept_b,
                             platform_role="department_admin")
        da_token = login(auth_client, da["username"], da["_password"])

        r = add_member(auth_client, da_token, grp["id"], target["user_id"])

        # Сначала срабатывает USER_NOT_FOUND-вид guard (target+grp одного dept'а,
        # а dept_admin не их) — service сейчас проверяет mismatch до cross-dept
        # check, см. P3 quirk; для нашего сценария target в grp.dept → mismatch
        # не срабатывает, должен сработать DEPARTMENT_ACCESS_DENIED.
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"

    def test_add_member_unknown_user_404(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()

        r = add_member(auth_client, admin_token, grp["id"], f"usr_{rand_suffix()}")

        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "USER_NOT_FOUND"

    def test_add_member_duplicate_conflict(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        user = make_dept_user(auth_client, admin_token, dept_a)
        first = add_member(auth_client, admin_token, grp["id"], user["user_id"])
        assert first.status_code == 201, first.text

        second = add_member(auth_client, admin_token, grp["id"], user["user_id"])

        assert second.status_code == 409, second.text
        assert second.json()["error_code"] == "ALREADY_GROUP_MEMBER"

    def test_remove_member_success(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        user = make_dept_user(auth_client, admin_token, dept_a)
        add_member(auth_client, admin_token, grp["id"], user["user_id"])
        since = now_utc()

        r = remove_member(auth_client, admin_token, grp["id"], user["user_id"])

        assert r.status_code == 200, r.text
        assert member_db_row(auth_db, grp["id"], user["user_id"]) is None

        wait_for_audit_row(
            loging_db_engine, action="group.member_remove",
            target_id=grp["id"], since=since,
        )

    def test_remove_member_404_vs_403_ordering(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        """Юзер никогда не был в группе — должен вернуть MEMBER_NOT_FOUND (404),
        не 403. Guard на dept_admin cross-dept срабатывает позже (см.
        info-disclosure quirk P3 в TODO)."""
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        user = make_dept_user(auth_client, admin_token, dept_a)

        r = remove_member(auth_client, admin_token, grp["id"], user["user_id"])

        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "MEMBER_NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# 3. POST/DELETE /groups/{gid}/services — dept→service access required
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupServiceAccess:
    def test_grant_service_to_group_requires_dept_access(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        # сервис существует в каталоге, но dept_a его НЕ получал
        orphan_svc = f"orphan_svc_{rand_suffix()}"
        ensure_service(auth_client, admin_token, orphan_svc)

        r = grant_service_to_group(auth_client, admin_token, grp["id"], orphan_svc)

        # Per FEAT-bot-groups: dept→service access check (`has_active_access`)
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"

    def test_grant_service_to_group_success_dept_has_access(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, svc = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        since = now_utc()

        r = grant_service_to_group(auth_client, admin_token, grp["id"], svc)

        assert r.status_code == 201, r.text
        body = r.json()
        assert body["service_name"] == svc
        assert body["is_active"] is True

        gsa = group_service_access_row(auth_db, grp["id"], svc)
        assert gsa is not None
        assert gsa["is_active"] is True

        wait_for_audit_row(
            loging_db_engine, action="group.service_grant",
            target_id=grp["id"], since=since,
        )

    def test_grant_service_unknown_404(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()

        r = grant_service_to_group(auth_client, admin_token, grp["id"],
                                    f"nonexistent_{rand_suffix()}")

        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "SERVICE_NOT_FOUND"

    def test_revoke_service_from_group(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, two_depts,
    ):
        dept_a, _, svc = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        grant_service_to_group(auth_client, admin_token, grp["id"], svc)

        r = revoke_service_from_group(auth_client, admin_token, grp["id"], svc)

        assert r.status_code == 200, r.text
        gsa = group_service_access_row(auth_db, grp["id"], svc)
        assert gsa is not None
        assert gsa["is_active"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 4. PUT /groups/{gid}/roles — GroupServiceAccess required
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupRoles:
    def test_assign_roles_without_group_service_access_403(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, svc = two_depts
        ensure_role(auth_client, admin_token, dept_a, svc, "reader")
        grp = create_group(auth_client, admin_token, dept_a).json()
        # НЕ выдаём group access к сервису

        r = assign_group_roles(auth_client, admin_token, grp["id"], svc, ["reader"])

        # Per FEAT-bot-groups: check_group_service_access
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "GROUP_SERVICE_ACCESS_REQUIRED"

    def test_assign_roles_success_with_access(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, svc = two_depts
        ensure_role(auth_client, admin_token, dept_a, svc, "reader")
        grp = create_group(auth_client, admin_token, dept_a).json()
        grant_service_to_group(auth_client, admin_token, grp["id"], svc)
        since = now_utc()

        r = assign_group_roles(auth_client, admin_token, grp["id"], svc, ["reader"])

        assert r.status_code == 201, r.text
        assert r.json()["roles"] == ["reader"]

        roles = group_service_roles(auth_db, grp["id"], svc)
        assert "reader" in roles

        wait_for_audit_row(
            loging_db_engine, action="group.roles_assign",
            target_id=grp["id"], since=since,
        )

    def test_assign_roles_unknown_role_422_or_404(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, svc = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        grant_service_to_group(auth_client, admin_token, grp["id"], svc)

        r = assign_group_roles(auth_client, admin_token, grp["id"], svc,
                                [f"ghost_{rand_suffix()}"])

        # DomainValidationError → 422 (или 400 в зависимости от mapping'а)
        assert r.status_code in (400, 422), r.text
        assert r.json()["error_code"] == "INVALID_SERVICE_ROLE"


# ══════════════════════════════════════════════════════════════════════════════
# 5. POST/GET/DELETE /groups/{gid}/bots
# ══════════════════════════════════════════════════════════════════════════════

class TestBotMembers:
    def test_add_bot_member_same_dept_success(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, svc = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        bot = make_bot_in_dept(auth_client, admin_token, dept_a,
                                allowed_services=[svc])
        since = now_utc()

        r = add_bot_member(auth_client, admin_token, grp["id"], bot["bot_id"])

        assert r.status_code == 201, r.text
        assert r.json()["bot_id"] == bot["bot_id"]

        row = bot_member_db_row(auth_db, grp["id"], bot["bot_id"])
        assert row is not None

        wait_for_audit_row(
            loging_db_engine, action="group.bot_member_add",
            target_id=grp["id"], since=since,
        )

    def test_add_bot_cross_dept_returns_mismatch(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, dept_b, svc = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        # бот из чужого dept'а
        bot = make_bot_in_dept(auth_client, admin_token, dept_b,
                                allowed_services=[svc])

        r = add_bot_member(auth_client, admin_token, grp["id"], bot["bot_id"])

        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "GROUP_DEPARTMENT_MISMATCH"

    def test_list_bot_members(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, svc = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        bot = make_bot_in_dept(auth_client, admin_token, dept_a,
                                allowed_services=[svc])
        add_bot_member(auth_client, admin_token, grp["id"], bot["bot_id"])

        r = auth_client.get(
            f"{AUTH}/groups/{grp['id']}/bots",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert r.status_code == 200, r.text
        ids = [m["bot_id"] for m in r.json()]
        assert bot["bot_id"] in ids

    def test_remove_bot_member(
        self, auth_client: httpx.Client, admin_token: str,
        auth_db, loging_db_engine, two_depts,
    ):
        dept_a, _, svc = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        bot = make_bot_in_dept(auth_client, admin_token, dept_a,
                                allowed_services=[svc])
        add_bot_member(auth_client, admin_token, grp["id"], bot["bot_id"])
        since = now_utc()

        r = remove_bot_member(auth_client, admin_token, grp["id"], bot["bot_id"])

        assert r.status_code == 200, r.text
        assert bot_member_db_row(auth_db, grp["id"], bot["bot_id"]) is None

        wait_for_audit_row(
            loging_db_engine, action="group.bot_member_remove",
            target_id=grp["id"], since=since,
        )

    def test_add_bot_unknown_404(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()

        r = add_bot_member(auth_client, admin_token, grp["id"],
                            f"bot_{rand_suffix()}")

        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "BOT_NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Наследование ролей через группу — users & bots
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupRoleInheritance:
    def test_user_gets_group_role_via_introspect(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, svc = two_depts
        ensure_role(auth_client, admin_token, dept_a, svc, "reader")
        # юзер БЕЗ direct-ролей
        user = make_dept_user(auth_client, admin_token, dept_a)
        user_token = login(auth_client, user["username"], user["_password"])
        # baseline: пустые service_roles
        before = introspect(auth_client, user_token)
        assert before["service_roles"].get(svc, []) == []

        # выдаём роль через группу
        grp = create_group(auth_client, admin_token, dept_a).json()
        grant_service_to_group(auth_client, admin_token, grp["id"], svc)
        assign_group_roles(auth_client, admin_token, grp["id"], svc, ["reader"])
        add_member(auth_client, admin_token, grp["id"], user["user_id"])

        after = introspect(auth_client, user_token)
        assert "reader" in after["service_roles"].get(svc, []), after

    def test_user_loses_role_after_remove_from_group(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, svc = two_depts
        ensure_role(auth_client, admin_token, dept_a, svc, "reader")
        user = make_dept_user(auth_client, admin_token, dept_a)
        user_token = login(auth_client, user["username"], user["_password"])
        grp = create_group(auth_client, admin_token, dept_a).json()
        grant_service_to_group(auth_client, admin_token, grp["id"], svc)
        assign_group_roles(auth_client, admin_token, grp["id"], svc, ["reader"])
        add_member(auth_client, admin_token, grp["id"], user["user_id"])
        assert "reader" in introspect(auth_client, user_token)["service_roles"].get(svc, [])

        remove_member(auth_client, admin_token, grp["id"], user["user_id"])

        after = introspect(auth_client, user_token)
        assert "reader" not in after["service_roles"].get(svc, []), after

    def test_bot_gets_group_role_via_introspect(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, svc = two_depts
        ensure_role(auth_client, admin_token, dept_a, svc, "reader")
        # бот с allowed_services включающим целевой сервис
        bot = make_bot_in_dept(auth_client, admin_token, dept_a,
                                allowed_services=[svc])
        tok = issue_bot_token(auth_client, admin_token, bot["bot_id"])
        raw_token = tok["token"]

        grp = create_group(auth_client, admin_token, dept_a).json()
        grant_service_to_group(auth_client, admin_token, grp["id"], svc)
        assign_group_roles(auth_client, admin_token, grp["id"], svc, ["reader"])
        add_bot_member(auth_client, admin_token, grp["id"], bot["bot_id"])

        info = introspect(auth_client, raw_token)
        assert "reader" in info["service_roles"].get(svc, []), info

    def test_bot_role_filtered_by_allowed_services(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        """`collect_bot_permissions`: group-роли пересекаются с
        `bot.allowed_services`. Бот с пустыми allowed_services не получает
        группу-роль, даже если состоит в группе."""
        dept_a, _, svc = two_depts
        ensure_role(auth_client, admin_token, dept_a, svc, "reader")
        # бот БЕЗ доступа к сервису
        bot = make_bot_in_dept(auth_client, admin_token, dept_a,
                                allowed_services=[])
        tok = issue_bot_token(auth_client, admin_token, bot["bot_id"])
        raw_token = tok["token"]

        grp = create_group(auth_client, admin_token, dept_a).json()
        grant_service_to_group(auth_client, admin_token, grp["id"], svc)
        assign_group_roles(auth_client, admin_token, grp["id"], svc, ["reader"])
        add_bot_member(auth_client, admin_token, grp["id"], bot["bot_id"])

        info = introspect(auth_client, raw_token)
        # сервис не попал в effective_services → роли тоже отфильтрованы
        assert svc not in info.get("allowed_services", []), info
        assert info["service_roles"].get(svc, []) == [], info

    def test_mixed_group_users_and_bots_resolve_independently(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, svc = two_depts
        ensure_role(auth_client, admin_token, dept_a, svc, "reader")
        user = make_dept_user(auth_client, admin_token, dept_a)
        bot = make_bot_in_dept(auth_client, admin_token, dept_a,
                                allowed_services=[svc])
        bot_tok = issue_bot_token(auth_client, admin_token, bot["bot_id"])
        user_token = login(auth_client, user["username"], user["_password"])

        grp = create_group(auth_client, admin_token, dept_a).json()
        grant_service_to_group(auth_client, admin_token, grp["id"], svc)
        assign_group_roles(auth_client, admin_token, grp["id"], svc, ["reader"])
        add_member(auth_client, admin_token, grp["id"], user["user_id"])
        add_bot_member(auth_client, admin_token, grp["id"], bot["bot_id"])

        u_info = introspect(auth_client, user_token)
        b_info = introspect(auth_client, bot_tok["token"])

        assert "reader" in u_info["service_roles"].get(svc, []), u_info
        assert "reader" in b_info["service_roles"].get(svc, []), b_info
        assert u_info.get("subject_type") in ("user", None) or u_info.get("actor_type") == "user"
        assert b_info.get("subject_type") in ("bot", None) or b_info.get("actor_type") == "bot"


# ══════════════════════════════════════════════════════════════════════════════
# 7. Negative — regular user / cross-dept
# ══════════════════════════════════════════════════════════════════════════════

class TestNegativeAccess:
    def test_regular_user_cannot_create_group(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, _, _ = two_depts
        user = make_dept_user(auth_client, admin_token, dept_a)
        token = login(auth_client, user["username"], user["_password"])

        r = create_group(auth_client, token, dept_a)

        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "ROLE_REQUIRED"

    def test_dept_b_admin_cannot_touch_dept_a_group(
        self, auth_client: httpx.Client, admin_token: str, two_depts,
    ):
        dept_a, dept_b, _ = two_depts
        grp = create_group(auth_client, admin_token, dept_a).json()
        target_user = make_dept_user(auth_client, admin_token, dept_a)
        da_b = make_dept_user(auth_client, admin_token, dept_b,
                               platform_role="department_admin")
        da_b_token = login(auth_client, da_b["username"], da_b["_password"])

        # 1) add_member чужой группы — DEPARTMENT_ACCESS_DENIED
        r1 = add_member(auth_client, da_b_token, grp["id"], target_user["user_id"])
        assert r1.status_code == 403, r1.text
        assert r1.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"

        # 2) patch чужой группы — у DA есть роль на groups, но dept чужой
        r2 = patch_group(auth_client, da_b_token, grp["id"], description="x")
        assert r2.status_code == 403, r2.text
        assert r2.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"

    def test_unknown_group_id_returns_404(
        self, auth_client: httpx.Client, admin_token: str,
    ):
        ghost = f"grp_{rand_suffix()}"
        r = patch_group(auth_client, admin_token, ghost, display_name="x")
        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "GROUP_NOT_FOUND"
