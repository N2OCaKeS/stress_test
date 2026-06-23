"""Тесты: GET /api/auth/v1/users/locked — admin видит юзеров под brute-force lockout'ом.

Locked = `locked_until > now`. `include_failing=true` дополнительно показывает
юзеров с накопленными неудачами, ещё не залоченных. Authz — как /unlock:
account_admin все отделы, department_admin только свой, обычный юзер — 403.
"""

from datetime import timedelta

from sqlalchemy import update

from src.models import User
from src.utils.time import utcnow

LOCKED_URL = "/api/auth/v1/users/locked"


async def _list_locked(client, token, include_failing=False):
    params = {"include_failing": "true"} if include_failing else {}
    return await client.get(
        LOCKED_URL,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )


async def _set_lockout(db, user_id, *, minutes_ahead=None, failed=0):
    locked_until = utcnow() + timedelta(minutes=minutes_ahead) if minutes_ahead is not None else None
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(locked_until=locked_until, failed_login_attempts=failed)
    )
    await db.commit()


# ── happy path ────────────────────────────────────────────────────────────────

async def test_actively_locked_user_appears(client, admin_token, user_a, db):
    await _set_lockout(db, user_a.id, minutes_ahead=15, failed=5)
    resp = await _list_locked(client, admin_token)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    entry = next((r for r in rows if r["user_id"] == user_a.id), None)
    assert entry is not None, "залоченный юзер должен быть в списке"
    assert entry["is_locked"] is True
    assert entry["failed_login_attempts"] == 5
    assert entry["locked_until"] is not None
    assert resp.headers.get("X-Total-Count") is not None


async def test_non_locked_user_absent(client, admin_token, user_a, db):
    # locked_until в прошлом → окно неактивно → не в списке.
    await _set_lockout(db, user_a.id, minutes_ahead=-5, failed=3)
    resp = await _list_locked(client, admin_token)
    assert resp.status_code == 200, resp.text
    assert all(r["user_id"] != user_a.id for r in resp.json())


async def test_include_failing_toggle(client, admin_token, user_a, db):
    # Накопил неудачи, но не залочен (locked_until=None).
    await _set_lockout(db, user_a.id, minutes_ahead=None, failed=2)

    without = await _list_locked(client, admin_token, include_failing=False)
    assert all(r["user_id"] != user_a.id for r in without.json())

    with_failing = await _list_locked(client, admin_token, include_failing=True)
    entry = next((r for r in with_failing.json() if r["user_id"] == user_a.id), None)
    assert entry is not None, "include_failing должен показать failing-but-not-locked"
    assert entry["is_locked"] is False
    assert entry["failed_login_attempts"] == 2


# ── authz / scoping ────────────────────────────────────────────────────────────

async def test_dept_admin_sees_only_own_dept(
    client, dept_admin_a_token, user_a, user_b, db
):
    await _set_lockout(db, user_a.id, minutes_ahead=15, failed=5)
    await _set_lockout(db, user_b.id, minutes_ahead=15, failed=5)
    resp = await _list_locked(client, dept_admin_a_token)
    assert resp.status_code == 200, resp.text
    ids = {r["user_id"] for r in resp.json()}
    assert user_a.id in ids, "свой отдел виден"
    assert user_b.id not in ids, "чужой отдел не виден"


async def test_account_admin_sees_all_depts(
    client, admin_token, user_a, user_b, db
):
    await _set_lockout(db, user_a.id, minutes_ahead=15, failed=5)
    await _set_lockout(db, user_b.id, minutes_ahead=15, failed=5)
    resp = await _list_locked(client, admin_token)
    assert resp.status_code == 200, resp.text
    ids = {r["user_id"] for r in resp.json()}
    assert {user_a.id, user_b.id} <= ids


async def test_regular_user_forbidden(client, user_a_token):
    resp = await _list_locked(client, user_a_token)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_REQUIRED"


# ── интеграция с /unlock ────────────────────────────────────────────────────────

async def test_unlock_removes_user_from_list(client, admin_token, user_a, db):
    await _set_lockout(db, user_a.id, minutes_ahead=15, failed=5)

    before = await _list_locked(client, admin_token)
    assert any(r["user_id"] == user_a.id for r in before.json())

    unlock = await client.post(
        f"/api/auth/v1/users/{user_a.id}/unlock",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert unlock.status_code == 200, unlock.text

    after = await _list_locked(client, admin_token)
    assert all(r["user_id"] != user_a.id for r in after.json())
