"""PATCH /users/{id} department_id change → purge group memberships старого отдела.

До фикса смена `user.department_id` деактивировала только `UserServiceRole`,
но оставляла `UserGroupMembership` к группам прежнего отдела. Через
`GroupServiceAccess`/`GroupServiceRole` эти группы продолжали тянуть за юзером
доступ и роли — privilege retention при transfer'е.

Фикс симметричен `user.roles_purged_on_transfer`: после смены `department_id`
удаляем memberships в группах старого отдела и эмитим
`user.groups_purged_on_transfer`.
"""

import pytest

URL = "/api/auth/v1/users/{user_id}"


@pytest.fixture()
def capture_audit_payloads(monkeypatch):
    """То же монипатчирование, что и в tests/users/test_update.py."""
    captured: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):
        captured.append(json)

    monkeypatch.setattr("src.services.audit_service.httpx.post", fake_sync_post)

    class _AsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json, headers):
            captured.append(json)

            class R:
                status_code = 201

            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type(
            "S",
            (),
            {"logging_service_url": "http://test", "logging_service_api_key": "k"},
        )(),
    )
    return captured


async def _make_group(db, department_id, name):
    from src.models.user_group import UserGroup
    from src.utils.ids import group_id

    grp = UserGroup(
        id=group_id(),
        department_id=department_id,
        name=name,
        display_name=name.title(),
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


async def _patch(client, token, user_id, body):
    return await client.patch(
        URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


class TestPatchDepartmentTransferPurgesGroups:
    async def test_dept_change_removes_memberships_in_old_dept(
        self, client, admin_token, user_a, dept_a, dept_b, db,
    ):
        """PATCH {department_id=other} → memberships юзера в группах
        старого отдела удалены, в группах нового — остаются (если есть)."""
        # Группа в старом отделе с юзером.
        old_group = await _make_group(db, dept_a.id, "purge_old_grp")
        await _add_user_to_group(db, old_group.id, user_a.id)
        # Группа в новом отделе тоже, на всякий случай — не должна задеваться.
        new_group = await _make_group(db, dept_b.id, "purge_new_grp")
        await _add_user_to_group(db, new_group.id, user_a.id)
        await db.commit()

        from src.repositories.groups import GroupRepository

        repo = GroupRepository(db)
        before_ids = {m.group_id for m in await repo.list_user_groups(user_a.id)}
        assert old_group.id in before_ids
        assert new_group.id in before_ids

        resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
        assert resp.status_code == 200, resp.text

        after_ids = {m.group_id for m in await repo.list_user_groups(user_a.id)}
        assert old_group.id not in after_ids, (
            f"membership в группе старого отдела должно быть удалено, осталось: {after_ids}"
        )
        # Группа нового отдела (несоответствие dept до transfer'а — отдельная
        # история, но фикс не должен трогать группы, чей department_id == new).
        assert new_group.id in after_ids

    async def test_dept_change_emits_groups_purged_audit(
        self, client, admin_token, user_a, dept_a, dept_b, db, capture_audit_payloads,
    ):
        """`user.groups_purged_on_transfer` эмитится ровно один раз с
        `removed_group_ids`, `old_dept_id`, `new_dept_id`."""
        grp1 = await _make_group(db, dept_a.id, "purge_audit_grp1")
        grp2 = await _make_group(db, dept_a.id, "purge_audit_grp2")
        await _add_user_to_group(db, grp1.id, user_a.id)
        await _add_user_to_group(db, grp2.id, user_a.id)
        await db.commit()

        original_dept_id = user_a.department_id

        resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
        assert resp.status_code == 200

        events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.groups_purged_on_transfer"
            and p.get("target_id") == user_a.id
        ]
        assert len(events) == 1, (
            f"expected 1 user.groups_purged_on_transfer, got {len(events)}"
        )
        details = events[0]["details"]
        assert details["old_dept_id"] == original_dept_id
        assert details["new_dept_id"] == dept_b.id
        assert set(details["removed_group_ids"]) == {grp1.id, grp2.id}

    async def test_no_group_memberships_no_audit(
        self, client, admin_token, user_a, dept_b, capture_audit_payloads,
    ):
        """Если у юзера нет memberships в старом отделе — audit не эмитится."""
        resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
        assert resp.status_code == 200

        events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.groups_purged_on_transfer"
        ]
        assert events == [], (
            f"без memberships в старом отделе audit не должен эмититься, got {events}"
        )

    async def test_no_dept_change_keeps_memberships(
        self, client, admin_token, user_a, dept_a, db,
    ):
        """PATCH без смены department_id — memberships не трогать."""
        grp = await _make_group(db, dept_a.id, "purge_noop_grp")
        await _add_user_to_group(db, grp.id, user_a.id)
        await db.commit()

        from src.repositories.groups import GroupRepository

        repo = GroupRepository(db)
        resp = await _patch(client, admin_token, user_a.id, {"email": "noop@example.com"})
        assert resp.status_code == 200

        after_ids = {m.group_id for m in await repo.list_user_groups(user_a.id)}
        assert grp.id in after_ids, (
            "PATCH без смены department_id не должен трогать memberships"
        )
