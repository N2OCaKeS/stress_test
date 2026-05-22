"""`GET /users` и `GET /users/department/{department_id}` — листинг пользователей."""

USERS_URL = "/api/auth/v1/users"
DEPT_USERS_URL = "/api/auth/v1/users/department/{department_id}"


# ── GET /users (только account_admin) ────────────────────────────────────────

class TestListAllUsers:
    async def test_account_admin_can_list(self, client, admin_token, dept_admin_a, user_a, user_b):
        resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        usernames = [u["username"] for u in resp.json()]
        assert "t_admin" in usernames
        assert "t_user_a" in usernames
        assert "t_user_b" in usernames

    async def test_account_admin_sees_at_least_self_when_only_one_user(self, client, admin_token):
        """Edge: только admin есть в БД — список не пустой, содержит admin'а."""
        resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert any(u["username"] == "t_admin" for u in body)

    async def test_dept_admin_forbidden(self, client, dept_admin_a_token):
        resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"})
        assert resp.status_code == 403

    async def test_regular_user_forbidden(self, client, user_a_token):
        resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {user_a_token}"})
        assert resp.status_code == 403

    async def test_unauthenticated_returns_401(self, client):
        resp = await client.get(USERS_URL)
        assert resp.status_code == 401


# ── GET /users/department/{dept_id} (account_admin + dept_admin) ─────────────

class TestListUsersByDepartment:
    async def test_account_admin_lists_any_dept(self, client, admin_token, dept_a, user_a, dept_b, user_b):
        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        usernames = [u["username"] for u in resp.json()]
        assert "t_user_a" in usernames
        # user_b — из dept_b — не должен светиться
        assert "t_user_b" not in usernames

    async def test_dept_admin_lists_own_department(self, client, dept_admin_a_token, dept_a, user_a):
        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 200
        assert any(u["username"] == "t_user_a" for u in resp.json())

    async def test_dept_admin_cannot_list_other_department(self, client, dept_admin_a_token, dept_b, user_b):
        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_b.id),
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"

    async def test_regular_user_forbidden(self, client, user_a_token, dept_a):
        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 403

    async def test_nonexistent_department_returns_404(self, client, admin_token):
        resp = await client.get(
            DEPT_USERS_URL.format(department_id="dep_does_not_exist"),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "DEPARTMENT_NOT_FOUND"

    async def test_empty_department_returns_empty_list(self, client, admin_token, dept_b):
        """Department без активных пользователей — пустой список, не 404."""
        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_b.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []
