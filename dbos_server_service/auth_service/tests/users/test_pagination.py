"""Пагинация list-эндпоинтов: GET /users, /bots, /groups.

`limit`/`offset` ограничивают страницу, `X-Total-Count` несёт полное число
записей в рамках scope-фильтра. Без query-параметров — backward-compatible
дефолт (отдаём список целиком до разумного капа).
"""

USERS_URL = "/api/auth/v1/users"
BOTS_URL = "/api/auth/v1/bots"
GROUPS_URL = "/api/auth/v1/groups"


async def _make_users(db, dept_id, n, prefix):
    from src.core.security import hash_password
    from src.models import User
    from src.utils.ids import _new_id

    out = []
    for i in range(n):
        u = User(
            id=_new_id("usr_"), username=f"{prefix}_{i}",
            password_hash=hash_password("User12345678!"),
            department_id=dept_id, is_active=True, status="active",
        )
        db.add(u)
        out.append(u)
    await db.flush()
    return out


# ── GET /users ────────────────────────────────────────────────────────────────


class TestUsersPagination:
    async def test_limit_caps_page_size(self, client, admin_token, dept_a, db):
        await _make_users(db, dept_a.id, 7, "pg_u")
        await db.commit()
        resp = await client.get(
            f"{USERS_URL}?limit=3",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    async def test_total_count_header_reflects_full_set(self, client, admin_token, dept_a, db):
        await _make_users(db, dept_a.id, 7, "pg_total")
        await db.commit()
        resp = await client.get(
            f"{USERS_URL}?limit=2",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        # 7 созданных + admin (account_admin тоже is_active).
        total = int(resp.headers["X-Total-Count"])
        assert total >= 8
        assert len(resp.json()) == 2

    async def test_offset_paginates_without_overlap(self, client, admin_token, dept_a, db):
        await _make_users(db, dept_a.id, 6, "pg_off")
        await db.commit()
        page1 = await client.get(
            f"{USERS_URL}?limit=3&offset=0",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        page2 = await client.get(
            f"{USERS_URL}?limit=3&offset=3",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        ids1 = {u["user_id"] for u in page1.json()}
        ids2 = {u["user_id"] for u in page2.json()}
        assert ids1.isdisjoint(ids2), "страницы не должны пересекаться"

    async def test_default_returns_full_list_backward_compat(self, client, admin_token, dept_a, db):
        """Без query-параметров — список как раньше (не урезан для малых наборов)."""
        await _make_users(db, dept_a.id, 4, "pg_bc")
        await db.commit()
        resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 5  # 4 + admin

    async def test_limit_over_cap_rejected_422(self, client, admin_token):
        resp = await client.get(
            f"{USERS_URL}?limit=99999",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    async def test_negative_offset_rejected_422(self, client, admin_token):
        resp = await client.get(
            f"{USERS_URL}?offset=-1",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422


# ── GET /users/department/{id} ─────────────────────────────────────────────────


class TestDeptUsersPagination:
    async def test_department_scope_total_excludes_other_dept(
        self, client, admin_token, dept_a, dept_b, db,
    ):
        await _make_users(db, dept_a.id, 5, "pg_da")
        await _make_users(db, dept_b.id, 9, "pg_db")
        await db.commit()
        resp = await client.get(
            f"{USERS_URL}/department/{dept_a.id}?limit=2",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        assert int(resp.headers["X-Total-Count"]) == 5


# ── GET /bots ───────────────────────────────────────────────────────────────


class TestBotsPagination:
    async def _make_bot(self, client, token, dept_id, name):
        return await client.post(
            BOTS_URL, headers={"Authorization": f"Bearer {token}"},
            json={"name": name, "department_id": dept_id, "allowed_services": []},
        )

    async def test_limit_and_total(self, client, admin_token, dept_a):
        for i in range(5):
            await self._make_bot(client, admin_token, dept_a.id, f"pg_bot_{i}")
        resp = await client.get(
            f"{BOTS_URL}?limit=2",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        assert int(resp.headers["X-Total-Count"]) == 5

    async def test_offset(self, client, admin_token, dept_a):
        for i in range(4):
            await self._make_bot(client, admin_token, dept_a.id, f"pg_botoff_{i}")
        p1 = await client.get(
            f"{BOTS_URL}?limit=2&offset=0",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        p2 = await client.get(
            f"{BOTS_URL}?limit=2&offset=2",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        ids1 = {b["bot_id"] for b in p1.json()}
        ids2 = {b["bot_id"] for b in p2.json()}
        assert ids1.isdisjoint(ids2)


# ── GET /groups ───────────────────────────────────────────────────────────────


class TestGroupsPagination:
    async def _make_group(self, client, token, dept_id, name):
        return await client.post(
            GROUPS_URL, headers={"Authorization": f"Bearer {token}"},
            json={"department_id": dept_id, "name": name},
        )

    async def test_limit_and_total(self, client, admin_token, dept_a):
        for i in range(5):
            await self._make_group(client, admin_token, dept_a.id, f"pg_grp_{i}")
        resp = await client.get(
            f"{GROUPS_URL}?limit=2",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        assert int(resp.headers["X-Total-Count"]) == 5

    async def test_default_lists_all_backward_compat(self, client, admin_token, dept_a):
        for i in range(3):
            await self._make_group(client, admin_token, dept_a.id, f"pg_grpbc_{i}")
        resp = await client.get(GROUPS_URL, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        names = [g["name"] for g in resp.json()]
        assert "pg_grpbc_0" in names
        assert "pg_grpbc_2" in names
