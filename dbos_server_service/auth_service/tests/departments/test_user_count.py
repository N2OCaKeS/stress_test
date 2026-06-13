"""Тесты: агрегат `user_count` в ответе отделов.

`user_count` — число пользователей, привязанных к отделу (все по
department_id, без фильтра по is_active; боты не считаются).
"""

from tests.conftest import _make_user

LIST_URL = "/api/auth/v1/departments"
PATCH_URL = "/api/auth/v1/departments/{dept_id}"


def _dept(payload, dept_id):
    for d in payload:
        if d["department_id"] == dept_id:
            return d
    raise AssertionError(f"dept {dept_id} not in response")


async def test_user_count_reflects_members(client, admin_token, dept_a, db):
    await _make_user(db, "uc_one", "User12345678!", department_id=dept_a.id)
    await _make_user(db, "uc_two", "User12345678!", department_id=dept_a.id)
    await db.flush()

    resp = await client.get(LIST_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert _dept(resp.json(), dept_a.id)["user_count"] == 2


async def test_user_count_empty_department_is_zero(client, admin_token, dept_a):
    resp = await client.get(LIST_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert _dept(resp.json(), dept_a.id)["user_count"] == 0


async def test_user_count_aggregate_is_per_department(client, admin_token, dept_a, dept_b, db):
    """Один агрегатный GROUP BY не путает отделы: счётчики независимы."""
    await _make_user(db, "uc_a1", "User12345678!", department_id=dept_a.id)
    await _make_user(db, "uc_a2", "User12345678!", department_id=dept_a.id)
    await _make_user(db, "uc_b1", "User12345678!", department_id=dept_b.id)
    await db.flush()

    resp = await client.get(LIST_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    payload = resp.json()
    assert _dept(payload, dept_a.id)["user_count"] == 2
    assert _dept(payload, dept_b.id)["user_count"] == 1


async def test_user_count_counts_inactive_users(client, admin_token, dept_a, db):
    """`user_count` считает всех привязанных, включая is_active=False."""
    await _make_user(db, "uc_active", "User12345678!", department_id=dept_a.id)
    inactive = await _make_user(db, "uc_inactive", "User12345678!", department_id=dept_a.id)
    inactive.is_active = False
    await db.flush()

    resp = await client.get(LIST_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert _dept(resp.json(), dept_a.id)["user_count"] == 2


async def test_patch_returns_user_count(client, admin_token, dept_a, db):
    """Одиночный PATCH-ответ (та же схема) тоже несёт актуальный `user_count`."""
    await _make_user(db, "uc_patch", "User12345678!", department_id=dept_a.id)
    await db.flush()

    resp = await client.patch(
        PATCH_URL.format(dept_id=dept_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"description": "touched"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_count"] == 1
