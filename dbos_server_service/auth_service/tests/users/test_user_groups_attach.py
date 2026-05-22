"""`POST /users/{user_id}/groups` и `DELETE /users/{user_id}/groups/{group_id}`.

С учётом per-department привязки групп (моя недавняя правка):

* группа в dept_a + user из dept_b → `GROUP_DEPARTMENT_MISMATCH` (мисматч физический);
* department_admin может управлять только в своём департаменте;
* regular user — 403;
* дубль — 409 `ALREADY_GROUP_MEMBER`;
* удаление не-члена — 404 `MEMBER_NOT_FOUND`;
* удаление последнего участника проходит штатно (нет защиты «нельзя оставить группу без участников»).
"""

GROUPS_URL = "/api/auth/v1/groups"
USERS_URL = "/api/auth/v1/users"


async def _create_group(client, token, department_id, name="users_test_grp"):
    return (await client.post(
        GROUPS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"department_id": department_id, "name": name, "display_name": name},
    )).json()


# ── POST attach ──────────────────────────────────────────────────────────────

class TestAttachUserToGroup:
    async def test_account_admin_attaches_own_dept_user(self, client, admin_token, dept_a, user_a):
        grp = await _create_group(client, admin_token, dept_a.id, name="attach_grp")
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        assert resp.status_code == 201

    async def test_cross_dept_user_returns_403(self, client, admin_token, dept_a, user_b):
        """group dept_a, user_b dept_b — отказ."""
        grp = await _create_group(client, admin_token, dept_a.id, name="cross_grp")
        resp = await client.post(
            f"{USERS_URL}/{user_b.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "GROUP_DEPARTMENT_MISMATCH"

    async def test_dept_admin_in_own_dept_succeeds(self, client, admin_token, dept_admin_a_token, dept_a, user_a):
        grp = await _create_group(client, admin_token, dept_a.id, name="da_grp")
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={"group_id": grp["id"]},
        )
        assert resp.status_code == 201

    async def test_dept_admin_other_dept_forbidden(self, client, admin_token, dept_admin_a_token, dept_b, user_b):
        grp = await _create_group(client, admin_token, dept_b.id, name="da_other_grp")
        resp = await client.post(
            f"{USERS_URL}/{user_b.id}/groups",
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
            json={"group_id": grp["id"]},
        )
        assert resp.status_code == 403

    async def test_regular_user_cannot_attach(self, client, admin_token, user_a_token, dept_a, user_a):
        grp = await _create_group(client, admin_token, dept_a.id, name="reg_grp")
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"group_id": grp["id"]},
        )
        assert resp.status_code == 403

    async def test_duplicate_membership_returns_409(self, client, admin_token, dept_a, user_a):
        grp = await _create_group(client, admin_token, dept_a.id, name="dup_grp")
        first = await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        assert first.status_code == 201
        second = await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        assert second.status_code == 409
        assert second.json()["error_code"] == "ALREADY_GROUP_MEMBER"

    async def test_attach_nonexistent_group_returns_404(self, client, admin_token, user_a):
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": "grp_ghost"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "GROUP_NOT_FOUND"

    async def test_attach_nonexistent_user_returns_404(self, client, admin_token, dept_a):
        grp = await _create_group(client, admin_token, dept_a.id, name="ghost_user_grp")
        resp = await client.post(
            f"{USERS_URL}/usr_nonexistent/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "USER_NOT_FOUND"


# ── DELETE detach ────────────────────────────────────────────────────────────

class TestDetachUserFromGroup:
    async def test_account_admin_removes_member(self, client, admin_token, dept_a, user_a):
        grp = await _create_group(client, admin_token, dept_a.id, name="detach_grp")
        await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        resp = await client.delete(
            f"{USERS_URL}/{user_a.id}/groups/{grp['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200

    async def test_dept_admin_cannot_detach_in_other_dept(
        self, client, admin_token, dept_admin_a_token, dept_b, user_b,
    ):
        grp = await _create_group(client, admin_token, dept_b.id, name="da_cross_detach")
        await client.post(
            f"{USERS_URL}/{user_b.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        resp = await client.delete(
            f"{USERS_URL}/{user_b.id}/groups/{grp['id']}",
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 403

    async def test_regular_user_cannot_detach(self, client, admin_token, user_a_token, dept_a, user_a):
        grp = await _create_group(client, admin_token, dept_a.id, name="reg_detach")
        await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        resp = await client.delete(
            f"{USERS_URL}/{user_a.id}/groups/{grp['id']}",
            headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 403

    async def test_detach_non_member_returns_404(self, client, admin_token, dept_a, user_a):
        grp = await _create_group(client, admin_token, dept_a.id, name="ghost_detach")
        resp = await client.delete(
            f"{USERS_URL}/{user_a.id}/groups/{grp['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "MEMBER_NOT_FOUND"

    async def test_detach_nonexistent_group_returns_404(self, client, admin_token, user_a):
        resp = await client.delete(
            f"{USERS_URL}/{user_a.id}/groups/grp_ghost",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    async def test_removing_last_member_keeps_group_alive(self, client, admin_token, dept_a, user_a):
        """Защиты «нельзя оставить группу без членов» сейчас нет — группа продолжает
        существовать. Тест фиксирует текущее поведение."""
        grp = await _create_group(client, admin_token, dept_a.id, name="last_member")
        await client.post(
            f"{USERS_URL}/{user_a.id}/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"group_id": grp["id"]},
        )
        resp = await client.delete(
            f"{USERS_URL}/{user_a.id}/groups/{grp['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        # Группа всё ещё существует и видима в списке.
        list_resp = await client.get(GROUPS_URL, headers={"Authorization": f"Bearer {admin_token}"})
        assert any(g["id"] == grp["id"] for g in list_resp.json())
