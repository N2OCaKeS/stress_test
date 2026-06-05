"""Tests for `GET /api/auth/v1/users/{user_id}/permissions`.

The endpoint returns a complete snapshot of a user's permissions:

* direct service-roles (raw `UserServiceRole` rows with `assigned_at` /
  `assigned_by`);
* group memberships with each group's `service_accesses` + `service_roles`;
* effective view (merged + INTERSECT с `allowed_services`, через
  `collect_user_permissions`);
* `platform_role` / `department_*` / `status` / `is_active` / `is_banned`.

Access guard mirrors `list_user_groups`:

* `account_admin`           — может смотреть любого юзера;
* `department_admin`        — только юзеров своего отдела (иначе 403
                              `DEPARTMENT_ACCESS_DENIED`);
* сам юзер (`identity.user_id == user_id`) — может смотреть себя;
* иначе                     — 403 `PERMISSION_DENIED`.

404 `USER_NOT_FOUND` отдаётся только когда target существует И actor имел
право смотреть — иначе схлопывается в 403, чтобы не превращать endpoint в
ID-enumeration oracle.

Тесты используют **реальный PostgreSQL** (никаких моков БД), audit-events
интерсептятся через `monkeypatch.setattr(audit_service, "emit", ...)`.
"""

from tests.conftest import (
    _assign_role,
    _grant_service,
    _make_role_def,
    _make_service,
)


URL_TPL = "/api/auth/v1/users/{user_id}/permissions"


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _create_group_via_db(db, department_id, name, display_name=None):
    """Создаёт `UserGroup` напрямую в БД — service_role на группу
    добавляется ниже через `_grant_group_service` / `_assign_group_role`,
    т.к. соответствующие HTTP-endpoint'ы требуют account_admin.
    """
    from src.models.user_group import UserGroup
    from src.utils.ids import group_id

    grp = UserGroup(
        id=group_id(),
        department_id=department_id,
        name=name,
        display_name=display_name or name.title(),
        is_active=True,
    )
    db.add(grp)
    await db.flush()
    return grp


async def _add_user_to_group(db, group_id_, user_id):
    from src.models.user_group_membership import UserGroupMembership
    from src.utils.ids import group_membership_id

    m = UserGroupMembership(
        id=group_membership_id(),
        group_id=group_id_,
        user_id=user_id,
    )
    db.add(m)
    await db.flush()
    return m


async def _grant_group_service(db, group_id_, service_name):
    from src.models.group_service_access import GroupServiceAccess
    from src.utils.ids import group_service_access_id

    obj = GroupServiceAccess(
        id=group_service_access_id(),
        group_id=group_id_,
        service_name=service_name,
        is_active=True,
    )
    db.add(obj)
    await db.flush()
    return obj


async def _assign_group_role(db, group_id_, service_name, role):
    from src.models.group_service_role import GroupServiceRole
    from src.utils.ids import group_service_role_id

    obj = GroupServiceRole(
        id=group_service_role_id(),
        group_id=group_id_,
        service_name=service_name,
        role=role,
        is_active=True,
    )
    db.add(obj)
    await db.flush()
    return obj


# ── 1. account_admin sees user permissions in any department ──────────────────


class TestAccountAdminCanSeeAnyUser:
    async def test_account_admin_sees_user_permissions_any_dept(
        self, client, admin_token, dept_b, user_b,
    ):
        """account_admin cross-dept by design (зеркало `list_users`)."""
        resp = await client.get(
            URL_TPL.format(user_id=user_b.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == user_b.id
        assert body["username"] == user_b.username
        assert body["department_id"] == dept_b.id
        # platform_role у обычного юзера == None.
        assert body["platform_role"] is None
        assert body["is_active"] is True
        assert body["is_banned"] is False
        assert body["status"] == "active"
        # Ключи структуры — direct/groups/effective.
        assert "direct_service_roles" in body
        assert "groups" in body
        assert "allowed_services" in body
        assert "service_roles" in body


# ── 2. department_admin sees own-dept user ───────────────────────────────────


class TestDeptAdminOwnDept:
    async def test_dept_admin_sees_own_dept_user(
        self, client, dept_admin_a_token, dept_a, user_a,
    ):
        resp = await client.get(
            URL_TPL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == user_a.id
        assert body["department_id"] == dept_a.id


# ── 3. department_admin blocked cross-dept ───────────────────────────────────


class TestDeptAdminCrossDeptBlocked:
    async def test_dept_admin_blocked_cross_dept(
        self, client, dept_admin_a_token, dept_b, user_b,
    ):
        """dept_admin отдела A не должен видеть юзера отдела B → 403."""
        resp = await client.get(
            URL_TPL.format(user_id=user_b.id),
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


# ── 4. regular user sees self ────────────────────────────────────────────────


class TestUserSelfView:
    async def test_user_sees_self(self, client, user_a_token, user_a, dept_a):
        resp = await client.get(
            URL_TPL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == user_a.id
        assert body["username"] == user_a.username
        assert body["department_id"] == dept_a.id


# ── 5. regular user blocked from viewing other ────────────────────────────────


class TestUserCannotSeeOther:
    async def test_user_blocked_other_user(
        self, client, user_a_token, user_b,
    ):
        """user_a пытается смотреть user_b → 403 PERMISSION_DENIED.

        Намеренно гард срабатывает ДО `user_repo.get_by_id(target)` —
        чтобы не утечь информацию о существовании target юзера.
        """
        resp = await client.get(
            URL_TPL.format(user_id=user_b.id),
            headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"


# ── 6. Full response with direct + 2 groups ───────────────────────────────────


class TestResponseStructure:
    async def test_response_includes_direct_and_group_roles(
        self,
        client, admin_token, db, dept_a, dept_a_with_service, service_x, user_a,
    ):
        """user_a в dept_a с service_x (`reader` direct + 2 группы):

        * direct role: `service_x.operator` (через `_assign_role`);
        * группа `eng_team`: service_x.access + 2 роли (operator, guest);
        * группа `sec_team`: service_y.access + role admin (service_y тоже
          гранится в dept).
        """
        # ── service_y: ещё один сервис, чтобы получить разнообразие ────────
        svc_y = await _make_service(db, "service_y")
        await _grant_service(db, dept_a.id, svc_y.service_name)

        # ── direct role на service_x ────────────────────────────────────────
        await _assign_role(db, user_a.id, service_x.service_name, "operator")

        # ── group #1: eng_team, service_x access + roles ─────────────────────
        eng = await _create_group_via_db(db, dept_a.id, "eng_team")
        await _add_user_to_group(db, eng.id, user_a.id)
        await _grant_group_service(db, eng.id, service_x.service_name)
        await _assign_group_role(db, eng.id, service_x.service_name, "operator")
        await _assign_group_role(db, eng.id, service_x.service_name, "guest")

        # ── group #2: sec_team, service_y access + role admin ───────────────
        sec = await _create_group_via_db(db, dept_a.id, "sec_team")
        await _add_user_to_group(db, sec.id, user_a.id)
        await _grant_group_service(db, sec.id, svc_y.service_name)
        await _assign_group_role(db, sec.id, svc_y.service_name, "admin")
        await db.commit()

        resp = await client.get(
            URL_TPL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # ── direct_service_roles: должно быть 2 строки на service_x ────────
        # (`reader` из фикстуры user_a + `operator` добавленный выше).
        direct = body["direct_service_roles"]
        direct_pairs = {(d["service_name"], d["role_name"]) for d in direct}
        assert (service_x.service_name, "reader") in direct_pairs
        assert (service_x.service_name, "operator") in direct_pairs
        # Каждая запись несёт `assigned_at` и опциональный `assigned_by`.
        for entry in direct:
            assert "assigned_at" in entry
            assert "assigned_by" in entry

        # ── groups: обе группы должны присутствовать ────────────────────────
        groups = body["groups"]
        assert len(groups) == 2
        group_names = {g["group_name"] for g in groups}
        assert group_names == {"eng_team", "sec_team"}

        eng_resp = next(g for g in groups if g["group_name"] == "eng_team")
        assert eng_resp["department_id"] == dept_a.id
        assert any(
            a["service_name"] == service_x.service_name
            for a in eng_resp["service_accesses"]
        )
        eng_role_pairs = {
            (r["service_name"], r["role_name"]) for r in eng_resp["service_roles"]
        }
        assert (service_x.service_name, "operator") in eng_role_pairs
        assert (service_x.service_name, "guest") in eng_role_pairs

        sec_resp = next(g for g in groups if g["group_name"] == "sec_team")
        assert any(
            a["service_name"] == svc_y.service_name
            for a in sec_resp["service_accesses"]
        )
        assert any(
            r["service_name"] == svc_y.service_name and r["role_name"] == "admin"
            for r in sec_resp["service_roles"]
        )

        # ── effective layer: merged + INTERSECT ──────────────────────────────
        allowed = set(body["allowed_services"])
        # service_x: dept access + group access (eng_team) — должен быть.
        assert service_x.service_name in allowed
        # service_y: dept access + group access (sec_team) — тоже.
        assert svc_y.service_name in allowed
        roles_view = body["service_roles"]
        # service_x effective roles = direct(reader, operator) ∪
        #                              eng_team(operator, guest)
        #                            = {reader, operator, guest}
        assert set(roles_view.get(service_x.service_name, [])) == {
            "reader", "operator", "guest",
        }
        # service_y effective roles = sec_team(admin)
        assert set(roles_view.get(svc_y.service_name, [])) == {"admin"}

    async def test_account_admin_has_empty_effective_layer(
        self, client, admin_token, account_admin,
    ):
        """account_admin'у мы возвращаем пустой effective layer (зеркало
        `IdentityContext` — admin grants implicit через `platform_role`,
        а не через service_roles). UI не должен думать «у админа всего N
        сервисов»."""
        resp = await client.get(
            URL_TPL.format(user_id=account_admin.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["platform_role"] == "account_admin"
        assert body["allowed_services"] == []
        assert body["service_roles"] == {}


# ── 7. 404 unknown user (only for admins/self path) ──────────────────────────


class TestUnknownUser:
    async def test_404_unknown_user(self, client, admin_token):
        resp = await client.get(
            URL_TPL.format(user_id="usr_does_not_exist"),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"


# ── 8. Audit emit (success + denied) ─────────────────────────────────────────


class TestAuditEmit:
    async def test_audit_emit_on_success(
        self, client, admin_token, user_a, monkeypatch,
    ):
        from src.services import audit_service as audit_mod

        captured: list[dict] = []
        original_emit = audit_mod.emit

        def _capture(action, actor_id=None, **kw):
            captured.append({"action": action, "actor_id": actor_id, **kw})
            return original_emit(action, actor_id, **kw)

        monkeypatch.setattr(audit_mod, "emit", _capture)

        resp = await client.get(
            URL_TPL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200

        view_events = [
            e for e in captured if e["action"] == "user.permissions_view"
        ]
        assert view_events, (
            f"expected user.permissions_view emit, got: "
            f"{[e['action'] for e in captured]}"
        )
        ok_events = [e for e in view_events if e.get("status") == "success"]
        assert ok_events, "expected at least one status=success emit"
        first_ok = ok_events[0]
        assert first_ok.get("target_id") == user_a.id
        details = first_ok.get("details") or {}
        assert details.get("target_username") == user_a.username
        # счётчики direct/groups присутствуют.
        assert "direct_roles_count" in details
        assert "groups_count" in details
        assert "allowed_services_count" in details

    async def test_audit_emit_on_denied(
        self, client, user_a_token, user_b, monkeypatch,
    ):
        from src.services import audit_service as audit_mod

        captured: list[dict] = []
        original_emit = audit_mod.emit

        def _capture(action, actor_id=None, **kw):
            captured.append({"action": action, "actor_id": actor_id, **kw})
            return original_emit(action, actor_id, **kw)

        monkeypatch.setattr(audit_mod, "emit", _capture)

        # user_a (regular) пытается смотреть user_b → 403 PERMISSION_DENIED.
        resp = await client.get(
            URL_TPL.format(user_id=user_b.id),
            headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 403

        denied = [
            e for e in captured
            if e["action"] == "user.permissions_view"
            and e.get("status") == "failure"
        ]
        assert denied, (
            f"expected denied emit, got: "
            f"{[(e['action'], e.get('status')) for e in captured]}"
        )
        first = denied[0]
        assert first.get("allowed") is False
        assert first.get("target_id") == user_b.id
        details = first.get("details") or {}
        # reason — `not_self_not_admin` для regular-user gard'а.
        assert details.get("reason") == "not_self_not_admin"


# ── 9. Audit emit on dept-admin cross-dept denied ────────────────────────────


class TestAuditEmitDeptAdminCrossDept:
    """Отдельный класс — gard срабатывает уже после lookup'а target'а,
    другой код-путь чем для regular-user. Проверяем именно эту ветку.
    """

    async def test_dept_admin_cross_dept_emits_denied(
        self, client, dept_admin_a_token, user_b, monkeypatch,
    ):
        from src.services import audit_service as audit_mod

        captured: list[dict] = []
        original_emit = audit_mod.emit

        def _capture(action, actor_id=None, **kw):
            captured.append({"action": action, "actor_id": actor_id, **kw})
            return original_emit(action, actor_id, **kw)

        monkeypatch.setattr(audit_mod, "emit", _capture)

        resp = await client.get(
            URL_TPL.format(user_id=user_b.id),
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 403
        denied = [
            e for e in captured
            if e["action"] == "user.permissions_view"
            and e.get("status") == "failure"
        ]
        assert len(denied) == 1
        details = denied[0].get("details") or {}
        assert details.get("reason") == "cross_department_user"


# ── 10. Stale membership на soft-deleted группу — скип ───────────────────────


class TestStaleMembershipOnSoftDeletedGroup:
    """`list_active_by_ids` отдаёт только `is_active=True` группы; если у
    юзера осталась membership на уже soft-deleted (`is_active=False`)
    группу — в ответ /permissions она попадать не должна (см.
    `user_service.get_user_permissions:1275-1280`).

    Проверяем именно через HTTP, чтобы exercise'нуть и сам endpoint,
    и фильтр в сервисе.
    """

    async def test_soft_deleted_group_skipped_in_response(
        self,
        client, admin_token, db, dept_a, dept_a_with_service, service_x, user_a,
    ):
        # Активная группа — нормально видна.
        live = await _create_group_via_db(db, dept_a.id, "live_team")
        await _add_user_to_group(db, live.id, user_a.id)
        await _grant_group_service(db, live.id, service_x.service_name)
        await _assign_group_role(db, live.id, service_x.service_name, "operator")

        # Soft-deleted: membership остаётся, группа is_active=False.
        ghost = await _create_group_via_db(db, dept_a.id, "ghost_team")
        await _add_user_to_group(db, ghost.id, user_a.id)
        await _grant_group_service(db, ghost.id, service_x.service_name)
        await _assign_group_role(db, ghost.id, service_x.service_name, "admin")
        ghost.is_active = False
        await db.flush()
        await db.commit()

        resp = await client.get(
            URL_TPL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        group_names = {g["group_name"] for g in body["groups"]}
        assert "live_team" in group_names
        assert "ghost_team" not in group_names, (
            "soft-deleted группа не должна попадать в /permissions "
            "(stale membership-skip)"
        )

        # Effective roles тоже не должны включать ghost-роль `admin`:
        # live даёт `operator` + direct `reader` из фикстуры.
        effective_x = set(body["service_roles"].get(service_x.service_name, []))
        assert "admin" not in effective_x, (
            f"роль admin из soft-deleted группы не должна попадать в "
            f"effective service_roles, got {effective_x}"
        )
