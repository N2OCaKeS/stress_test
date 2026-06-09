"""status_filter=active и status_filter=blocked для GET /users и GET /users/department/{id}.

SQL-фильтр передаётся как в list_all, так и в count_active — total и страница
должны быть согласованы для всех трёх статусов (active/banned/blocked).
Тесты на `status=banned` живут в `test_list_users_pagination_status.py`; здесь
только `active` и `blocked`.
"""

from sqlalchemy import update

from src.models import User

USERS_URL = "/api/auth/v1/users"
DEPT_USERS_URL = "/api/auth/v1/users/department/{department_id}"


async def _make_users(db, dept_id, n, prefix, status="active"):
    from src.core.security import hash_password
    from src.utils.ids import _new_id

    out = []
    for i in range(n):
        u = User(
            id=_new_id("usr_"), username=f"{prefix}_{i}",
            password_hash=hash_password("User12345678!"),
            department_id=dept_id, is_active=(status == "active"), status=status,
        )
        db.add(u)
        out.append(u)
    await db.flush()
    return out


async def _block_user(db, user_id):
    await db.execute(
        update(User).where(User.id == user_id).values(status="blocked", is_active=False)
    )
    await db.commit()


class TestStatusActiveFilter:
    async def test_status_active_total_excludes_banned_users(
        self, client, admin_token, dept_a, user_a, db,
    ):
        """`status=active` → total не включает banned пользователей."""
        # Добавляем banned-шум
        banned = await _make_users(db, dept_a.id, 3, "sa_banned", status="banned")
        await db.commit()
        # user_a остаётся активным
        resp = await client.get(
            USERS_URL,
            params={"status": "active", "limit": 50},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        total = int(resp.headers["X-Total-Count"])
        body = resp.json()
        # Все вернувшиеся — строго active
        assert all(u["status"] == "active" for u in body)
        # Total не включает banned
        banned_ids = {u.id for u in banned}
        returned_ids = {u["user_id"] for u in body}
        assert returned_ids.isdisjoint(banned_ids), (
            f"status=active вернул banned user_ids: {returned_ids & banned_ids}"
        )
        # total согласован со страницей (не завышен из-за banned)
        assert total == len(body)

    async def test_status_active_total_excludes_blocked_users(
        self, client, admin_token, dept_a, user_a, user_b, db,
    ):
        """`status=active` total не включает заблокированных."""
        await _block_user(db, user_b.id)

        resp = await client.get(
            USERS_URL,
            params={"status": "active", "limit": 50},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        returned_ids = {u["user_id"] for u in body}
        assert user_b.id not in returned_ids
        # user_a — active, должен присутствовать
        assert user_a.id in returned_ids

    async def test_status_active_page_and_total_consistent(
        self, client, admin_token, dept_a, user_a, db,
    ):
        """offset поверх status=active: страница уменьшается, total постоянен."""
        active_users = await _make_users(db, dept_a.id, 5, "sa_pg_active", status="active")
        await db.commit()

        first = await client.get(
            USERS_URL,
            params={"status": "active", "limit": 3, "offset": 0},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert first.status_code == 200
        total_first = int(first.headers["X-Total-Count"])

        second = await client.get(
            USERS_URL,
            params={"status": "active", "limit": 3, "offset": 3},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert second.status_code == 200
        total_second = int(second.headers["X-Total-Count"])

        # total одинаков на обеих страницах
        assert total_first == total_second, (
            f"total должен быть постоянен между страницами: {total_first} != {total_second}"
        )
        # Оба куска не пересекаются
        ids_first = {u["user_id"] for u in first.json()}
        ids_second = {u["user_id"] for u in second.json()}
        assert ids_first.isdisjoint(ids_second), "страницы не должны перекрываться"


class TestStatusBlockedFilter:
    async def test_status_blocked_returns_only_blocked(
        self, client, admin_token, dept_a, user_a, user_b, db,
    ):
        """`status=blocked` возвращает только blocked пользователей."""
        await _block_user(db, user_a.id)

        resp = await client.get(
            USERS_URL,
            params={"status": "blocked", "limit": 50},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        total = int(resp.headers["X-Total-Count"])

        assert all(u["status"] == "blocked" for u in body), (
            f"status=blocked вернул не-blocked строки: {[u['status'] for u in body]}"
        )
        assert user_a.id in {u["user_id"] for u in body}
        # user_b не заблокирован — не должен попасть
        assert user_b.id not in {u["user_id"] for u in body}
        # total согласован со страницей
        assert total == len(body)

    async def test_status_blocked_total_not_inflated_by_active(
        self, client, admin_token, dept_a, user_a, db,
    ):
        """active-шум не попадает в total при status=blocked."""
        await _make_users(db, dept_a.id, 8, "sb_active_noise", status="active")
        await db.commit()
        await _block_user(db, user_a.id)

        resp = await client.get(
            USERS_URL,
            params={"status": "blocked", "limit": 10},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        total = int(resp.headers["X-Total-Count"])
        assert total == 1, f"total=blocked должен быть 1, got {total}"
        assert resp.json()[0]["user_id"] == user_a.id

    async def test_status_blocked_dept_scoped_excludes_other_dept(
        self, client, admin_token, dept_a, dept_b, user_a, user_b, db,
    ):
        """dept-scoped status=blocked не утекает в чужой отдел."""
        await _block_user(db, user_a.id)  # dept_a blocked
        await _block_user(db, user_b.id)  # dept_b blocked

        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            params={"status": "blocked", "limit": 10},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        total = int(resp.headers["X-Total-Count"])
        returned_ids = {u["user_id"] for u in resp.json()}
        assert total == 1
        assert user_a.id in returned_ids
        assert user_b.id not in returned_ids

    async def test_status_blocked_offset_consistent(
        self, client, admin_token, dept_a, user_a, db,
    ):
        """offset за пределами blocked-множества: страница пустая, total постоянен."""
        await _block_user(db, user_a.id)

        resp0 = await client.get(
            USERS_URL,
            params={"status": "blocked", "limit": 5, "offset": 0},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        total_at_0 = int(resp0.headers["X-Total-Count"])
        assert total_at_0 == 1

        resp_far = await client.get(
            USERS_URL,
            params={"status": "blocked", "limit": 5, "offset": 100},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp_far.status_code == 200
        assert int(resp_far.headers["X-Total-Count"]) == 1
        assert resp_far.json() == []
