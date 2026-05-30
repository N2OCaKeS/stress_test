"""Пагинация `GET /users` со status_filter и `X-Total-Count`.

До фикса `status_filter` применялся как Python-фильтр поверх уже срезанной
страницы, а `total` приходил из `count_active` без `status`-фильтра. Из-за
этого UX «список banned юзеров» был сломан: `X-Total-Count` показывал общее
число активных юзеров, а сама страница могла оказаться короче limit'а или
пустой при больших offset'ах.

Здесь проверяем: total ровно равен числу юзеров с заданным status'ом, а
страница и заголовок согласованы.
"""

USERS_URL = "/api/auth/v1/users"
DEPT_USERS_URL = "/api/auth/v1/users/department/{department_id}"
BAN_URL = "/api/auth/v1/users/{user_id}/ban"


async def _make_extra_users(db, dept_id, n, prefix):
    from src.core.security import hash_password
    from src.models import User
    from src.utils.ids import _new_id

    out = []
    for i in range(n):
        u = User(
            id=_new_id("usr_"), username=f"{prefix}_{i}",
            password_hash=hash_password("User1234!"),
            department_id=dept_id, is_active=True, status="active",
        )
        db.add(u)
        out.append(u)
    await db.flush()
    return out


async def _ban(client, admin_token, user_id):
    return await client.post(
        BAN_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ban_type": "permanent", "reason": "pagination_test"},
    )


class TestStatusFilterTotalCount:
    async def test_total_count_matches_banned_count_not_total_users(
        self, client, admin_token, dept_a, user_a, db,
    ):
        """`status=banned` → `X-Total-Count` ровно = числу banned юзеров.

        Создаём активных «шумных» юзеров (которые не должны попасть в total),
        баним ровно одного. Без SQL-фильтра total бы вернул 1 + N активных
        (и admin'а) — мы проверяем, что цифра совпадает с числом banned.
        """
        await _make_extra_users(db, dept_a.id, 12, "stf_active")
        await db.commit()

        ban_resp = await _ban(client, admin_token, user_a.id)
        assert ban_resp.status_code == 200, ban_resp.text

        resp = await client.get(
            USERS_URL,
            params={"status": "banned", "limit": 10},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        total = int(resp.headers["X-Total-Count"])

        assert total == 1, (
            f"total должен ровно отражать число banned юзеров, got {total}"
        )
        # И страница согласована с total — один banned, страница длины 1.
        assert len(body) == 1
        assert body[0]["status"] == "banned"
        assert body[0]["username"] == "t_user_a"

    async def test_total_count_grows_with_more_bans(
        self, client, admin_token, dept_a, user_a, user_b, db,
    ):
        """Дополнительные баны увеличивают total status=banned — без шума от active."""
        await _make_extra_users(db, dept_a.id, 5, "stf_grow_active")
        await db.commit()

        # Дополнительно создадим юзеров для бана
        banned_targets = await _make_extra_users(db, dept_a.id, 3, "stf_to_ban")
        await db.commit()

        for u in banned_targets:
            assert (await _ban(client, admin_token, u.id)).status_code == 200
        # user_a и user_b тоже банятся — итого 5 банов.
        assert (await _ban(client, admin_token, user_a.id)).status_code == 200
        assert (await _ban(client, admin_token, user_b.id)).status_code == 200

        resp = await client.get(
            USERS_URL,
            params={"status": "banned", "limit": 10},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        total = int(resp.headers["X-Total-Count"])
        assert total == 5, f"expected 5 banned total, got {total}"
        assert all(u["status"] == "banned" for u in resp.json())

    async def test_dept_status_total_excludes_other_dept(
        self, client, admin_token, dept_a, dept_b, user_a, user_b, db,
    ):
        """dept-scoped: total для status=banned ограничен этим отделом."""
        await _ban(client, admin_token, user_a.id)  # dept_a
        await _ban(client, admin_token, user_b.id)  # dept_b

        resp = await client.get(
            DEPT_USERS_URL.format(department_id=dept_a.id),
            params={"status": "banned", "limit": 10},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        total = int(resp.headers["X-Total-Count"])
        # Только user_a — banned в dept_a.
        assert total == 1
        usernames = {u["username"] for u in resp.json()}
        assert usernames == {"t_user_a"}

    async def test_status_filter_pagination_offset_is_consistent(
        self, client, admin_token, dept_a, user_a, db,
    ):
        """offset поверх status_filter не должен ронять total ниже фактического числа.

        Если бы `total` считался без status-фильтра, offset с большим значением
        на пустой странице давал бы X-Total-Count == общего числа active +
        banned юзеров (десятки), а сама страница — пустая. После фикса total
        соответствует именно банам, page может быть пустой только когда offset
        >= total.
        """
        await _make_extra_users(db, dept_a.id, 7, "stf_pg_active")
        await db.commit()
        await _ban(client, admin_token, user_a.id)

        # offset=0: одна banned, total=1.
        first = await client.get(
            USERS_URL,
            params={"status": "banned", "limit": 5, "offset": 0},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert first.status_code == 200
        assert int(first.headers["X-Total-Count"]) == 1
        assert len(first.json()) == 1

        # offset=10 (за пределами) — страница пустая, но total всё равно 1.
        empty = await client.get(
            USERS_URL,
            params={"status": "banned", "limit": 5, "offset": 10},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert empty.status_code == 200
        assert int(empty.headers["X-Total-Count"]) == 1
        assert empty.json() == []
