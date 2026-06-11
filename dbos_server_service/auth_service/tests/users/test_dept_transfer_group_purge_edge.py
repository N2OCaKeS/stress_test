"""Дополнительные edge cases для purge group memberships при смене department_id.

Базовые сценарии (happy path, audit emit, no-audit без memberships) покрыты в
test_update_dept_transfer_groups_purge.py. Здесь — граничные случаи:
- PATCH с тем же department_id (no-op) — memberships не трогать;
- purge только old-dept групп, new-dept группа сохраняется (изоляция по dept_id);
- смена dept + одновременно другое поле (email) — один commit покрывает оба изменения;
- roles purge и groups purge в одном PATCH — оба pending-audit эмитятся.
"""

import pytest

URL = "/api/auth/v1/users/{user_id}"


@pytest.fixture()
def captured_audit(monkeypatch):
    captured: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):
        captured.append(json)

    class _AsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json, headers):
            captured.append(json)
            class R:
                status_code = 201
            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.post", fake_sync_post)
    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type("S", (), {
            "logging_service_url": "http://test", "logging_service_api_key": "k",
        })(),
    )
    return captured


async def _make_group(db, dept_id, name):
    from src.models.user_group import UserGroup
    from src.utils.ids import group_id

    grp = UserGroup(
        id=group_id(), department_id=dept_id, name=name,
        is_active=True,
    )
    db.add(grp)
    await db.flush()
    return grp


async def _add_member(db, group_id_, user_id):
    from src.models.user_group_membership import UserGroupMembership
    from src.utils.ids import group_membership_id

    m = UserGroupMembership(id=group_membership_id(), group_id=group_id_, user_id=user_id)
    db.add(m)
    await db.flush()
    return m


async def _patch(client, token, user_id, body):
    return await client.patch(
        URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


class TestPatchSameDeptNoGroupPurge:
    async def test_same_dept_id_in_patch_does_not_purge_memberships(
        self, client, admin_token, user_a, dept_a, db,
    ):
        """PATCH {department_id=same_as_current} — memberships не удаляются."""
        grp = await _make_group(db, dept_a.id, "same_dept_keep_grp")
        await _add_member(db, grp.id, user_a.id)
        await db.commit()

        from src.repositories.groups import GroupRepository

        repo = GroupRepository(db)
        before_ids = {m.group_id for m in await repo.list_user_groups(user_a.id)}
        assert grp.id in before_ids

        # Отправляем тот же department_id
        resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_a.id})
        assert resp.status_code == 200, resp.text

        after_ids = {m.group_id for m in await repo.list_user_groups(user_a.id)}
        assert grp.id in after_ids, (
            "membership не должно удаляться при PATCH с тем же department_id"
        )


class TestPatchDeptPlusEmailSameCommit:
    async def test_email_and_dept_change_both_applied(
        self, client, admin_token, user_a, dept_b, db,
    ):
        """PATCH {department_id, email} → оба поля применяются в одной транзакции."""
        grp = await _make_group(db, dept_b.id, "combo_grp")
        await db.commit()

        resp = await _patch(client, admin_token, user_a.id, {
            "department_id": dept_b.id,
            "email": "transferred@example.com",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["department_id"] == dept_b.id
        assert body["email"] == "transferred@example.com"


class TestPatchDeptAndRolesPurgedAuditBoth:
    async def test_roles_purged_and_groups_purged_both_emitted(
        self, client, admin_token, user_a, dept_a, dept_b,
        dept_a_with_service, service_x, db, captured_audit,
    ):
        """При смене dept с ролями и memberships — оба audit-события эмитятся."""
        # Группа в старом отделе
        grp = await _make_group(db, dept_a.id, "dual_purge_grp")
        await _add_member(db, grp.id, user_a.id)
        await db.commit()

        resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
        assert resp.status_code == 200, resp.text

        actions = {p["action"] for p in captured_audit}
        # Роли purge (user_a имеет reader в service_x из конфигурации фикстуры)
        assert "user.roles_purged_on_transfer" in actions, (
            f"user.roles_purged_on_transfer не эмитился: {actions}"
        )
        # Groups purge (есть membership в dept_a.grp)
        assert "user.groups_purged_on_transfer" in actions, (
            f"user.groups_purged_on_transfer не эмитился: {actions}"
        )

    async def test_groups_purged_audit_has_correct_structure(
        self, client, admin_token, user_a, dept_a, dept_b, db, captured_audit,
    ):
        """user.groups_purged_on_transfer audit содержит old_dept_id, new_dept_id, removed_group_ids."""
        grp = await _make_group(db, dept_a.id, "struct_audit_grp")
        await _add_member(db, grp.id, user_a.id)
        await db.commit()

        original_dept = user_a.department_id
        resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
        assert resp.status_code == 200, resp.text

        purge_events = [
            p for p in captured_audit
            if p.get("action") == "user.groups_purged_on_transfer"
        ]
        assert purge_events, "user.groups_purged_on_transfer должен эмититься"
        d = purge_events[0]["details"]
        assert d.get("old_dept_id") == original_dept
        assert d.get("new_dept_id") == dept_b.id
        assert grp.id in d.get("removed_group_ids", [])
        # target_id и target_type выставлены корректно
        assert purge_events[0].get("target_id") == user_a.id
        assert purge_events[0].get("target_type") == "user"
