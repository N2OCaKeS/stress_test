"""Тесты: department_admin банит/разбанивает юзеров СВОЕГО отдела.

Ролевая модель: dep_admin имеет полный доступ к бизнес-данным своего
отдела, в т.ч. бан/разбан его юзеров. account_admin — любых. Чужой отдел
и платформенные юзеры (без department_id) — 403 DEPT_MISMATCH.
"""

from src.core.constants import UserStatus

BAN_URL = "/api/auth/v1/users/{user_id}/ban"
UNBAN_URL = "/api/auth/v1/users/{user_id}/unban"


async def _ban(client, token, user_id, reason="test ban"):
    return await client.post(
        BAN_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"ban_type": "permanent", "reason": reason},
    )


async def _unban(client, token, user_id):
    return await client.post(
        UNBAN_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
    )


# ── ban ──────────────────────────────────────────────────────────────────────

async def test_dep_admin_bans_own_dept_user(client, dept_admin_a_token, user_a, db):
    """dep_admin банит юзера своего отдела → 200, status BANNED."""
    resp = await _ban(client, dept_admin_a_token, user_a.id)
    assert resp.status_code == 200, resp.text
    await db.refresh(user_a)
    assert user_a.status == UserStatus.BANNED
    assert user_a.is_active is False


async def test_dep_admin_cross_dept_ban_403(client, dept_admin_a_token, user_b, db):
    """dep_admin банит юзера чужого отдела → 403 DEPT_MISMATCH, status не меняется."""
    resp = await _ban(client, dept_admin_a_token, user_b.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "DEPT_MISMATCH"
    await db.refresh(user_b)
    assert user_b.status != UserStatus.BANNED


async def test_dep_admin_cannot_ban_platform_admin(client, dept_admin_a_token, account_admin, db):
    """dep_admin банит account_admin (платформенный, без department_id) → 403."""
    resp = await _ban(client, dept_admin_a_token, account_admin.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "DEPT_MISMATCH"
    await db.refresh(account_admin)
    assert account_admin.status != UserStatus.BANNED


async def test_dep_admin_cannot_ban_self(client, dept_admin_a_token, dept_admin_a):
    """dep_admin банит себя → 422 CANNOT_BAN_SELF (self-guard раньше dept-check)."""
    resp = await _ban(client, dept_admin_a_token, dept_admin_a.id)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == "CANNOT_BAN_SELF"


async def test_account_admin_bans_any_user(client, admin_token, user_b, db):
    """account_admin банит юзера любого отдела → 200 (regression)."""
    resp = await _ban(client, admin_token, user_b.id)
    assert resp.status_code == 200, resp.text
    await db.refresh(user_b)
    assert user_b.status == UserStatus.BANNED


async def test_account_admin_cannot_ban_self(client, admin_token, account_admin):
    """account_admin банит себя → 422 CANNOT_BAN_SELF (regression)."""
    resp = await _ban(client, admin_token, account_admin.id)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == "CANNOT_BAN_SELF"


# ── unban ────────────────────────────────────────────────────────────────────

async def test_dep_admin_unbans_own_dept_user(client, admin_token, dept_admin_a_token, user_a, db):
    """dep_admin разбанивает юзера своего отдела → 200, status ACTIVE."""
    assert (await _ban(client, admin_token, user_a.id)).status_code == 200
    resp = await _unban(client, dept_admin_a_token, user_a.id)
    assert resp.status_code == 200, resp.text
    await db.refresh(user_a)
    assert user_a.status == UserStatus.ACTIVE
    assert user_a.is_active is True


async def test_dep_admin_cross_dept_unban_403(client, admin_token, dept_admin_a_token, user_b, db):
    """dep_admin разбанивает юзера чужого отдела → 403 DEPT_MISMATCH, бан остаётся."""
    assert (await _ban(client, admin_token, user_b.id)).status_code == 200
    resp = await _unban(client, dept_admin_a_token, user_b.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "DEPT_MISMATCH"
    await db.refresh(user_b)
    assert user_b.status == UserStatus.BANNED


async def test_dep_admin_cannot_unban_platform_admin(client, dept_admin_a_token, account_admin):
    """dep_admin разбанивает account_admin (без department_id) → 403.

    account_admin не забанен (себя забанить нельзя), но dept-scope check
    идёт до BAN_NOT_FOUND — DA вообще не должен дотянуться до платформенного
    юзера, поэтому ждём 403, а не 404.
    """
    resp = await _unban(client, dept_admin_a_token, account_admin.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "DEPT_MISMATCH"


async def test_account_admin_unbans_any_user(client, admin_token, user_b, db):
    """account_admin разбанивает юзера любого отдела → 200 (regression)."""
    assert (await _ban(client, admin_token, user_b.id)).status_code == 200
    resp = await _unban(client, admin_token, user_b.id)
    assert resp.status_code == 200, resp.text
    await db.refresh(user_b)
    assert user_b.status == UserStatus.ACTIVE


async def test_regular_user_cannot_unban(client, admin_token, user_a_token, user_b):
    """Обычный юзер → 403 ROLE_REQUIRED (AnyAdmin-guard)."""
    assert (await _ban(client, admin_token, user_b.id)).status_code == 200
    resp = await _unban(client, user_a_token, user_b.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_REQUIRED"
