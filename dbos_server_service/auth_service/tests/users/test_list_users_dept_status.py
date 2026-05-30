"""GET /users/department/{dept_id} — status_filter и include_banned.

W7: dept-уровень list_users_by_department не имел тестов на status_filter.
Базовые тесты global-листинга с include_banned/status в test_list_users.py;
здесь — аналогичные случаи, но для dept-scoped endpoint'а.
"""

DEPT_USERS_URL = "/api/auth/v1/users/department/{department_id}"
BAN_URL = "/api/auth/v1/users/{user_id}/ban"


async def _ban(client, admin_token, user_id):
    r = await client.post(
        BAN_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ban_type": "permanent", "reason": "test"},
    )
    assert r.status_code == 200, r.text


class TestDeptListStatusFilter:
    async def test_dept_status_banned_filter_returns_only_banned(
        self, client, admin_token, dept_a, user_a, user_b,
    ):
        """`status=banned` на dept endpoint возвращает только забаненных из этого отдела."""
        await _ban(client, admin_token, user_a.id)

        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            params={"status": "banned"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        statuses = {u["status"] for u in body}
        usernames = {u["username"] for u in body}

        assert statuses == {"banned"}, f"non-banned users leaked: {body}"
        assert "t_user_a" in usernames
        # user_b принадлежит dept_b — не должен появляться
        assert "t_user_b" not in usernames

    async def test_dept_status_active_filter_returns_only_active(
        self, client, admin_token, dept_a, user_a,
    ):
        """`status=active` — только активные пользователи из отдела."""
        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            params={"status": "active"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(u["status"] == "active" for u in body), (
            f"non-active users in response: {[u for u in body if u['status'] != 'active']}"
        )
        assert any(u["username"] == "t_user_a" for u in body)

    async def test_dept_status_banned_implied_include_banned(
        self, client, admin_token, dept_a, user_a,
    ):
        """`status=banned` без явного include_banned всё равно возвращает banned.

        Инвариант: `status_filter is not None` → `effective_include_banned=True`,
        иначе status=banned давал бы пустой список.
        """
        await _ban(client, admin_token, user_a.id)

        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            params={"status": "banned"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert any(u["username"] == "t_user_a" for u in resp.json())

    async def test_dept_status_invalid_returns_422(self, client, admin_token, dept_a):
        """`status=junk` → 422 (Pydantic enum validation)."""
        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            params={"status": "junk"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    async def test_dept_include_banned_plus_status_banned(
        self, client, admin_token, dept_a, user_a,
    ):
        """Явная комбинация include_banned=true + status=banned — возвращает banned."""
        await _ban(client, admin_token, user_a.id)

        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            params={"include_banned": "true", "status": "banned"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert any(u["username"] == "t_user_a" for u in body)
        assert all(u["status"] == "banned" for u in body)

    async def test_dept_admin_uses_status_filter_own_dept(
        self, client, admin_token, dept_admin_a_token, dept_a, user_a,
    ):
        """department_admin своего отдела с status=banned тоже видит забаненных."""
        await _ban(client, admin_token, user_a.id)

        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            params={"status": "banned"},
            headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        )
        assert resp.status_code == 200
        assert any(u["username"] == "t_user_a" for u in resp.json())
