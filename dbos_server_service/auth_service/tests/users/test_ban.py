"""Тесты: POST /api/auth/v1/users/{id}/ban и /unban — блокировка и разблокировка пользователей."""

BAN_URL = "/api/auth/v1/users/{user_id}/ban"
UNBAN_URL = "/api/auth/v1/users/{user_id}/unban"
LOGIN_URL = "/api/auth/v1/login"


async def _ban(client, token, user_id, reason="test ban"):
    return await client.post(
        BAN_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"ban_type": "manual", "reason": reason},
    )


async def test_admin_bans_user(client, admin_token, user_a):
    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 200


async def test_banned_user_cannot_login(client, admin_token, user_a):
    await _ban(client, admin_token, user_a.id)
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BANNED"


async def test_banned_user_refresh_fails(client, admin_token, user_a, user_a_token):
    login_resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})
    refresh_token = login_resp.json()["refresh_token"]
    await _ban(client, admin_token, user_a.id)
    resp = await client.post("/api/auth/v1/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code in (401, 403)


async def test_duplicate_ban_returns_409(client, admin_token, user_a):
    await _ban(client, admin_token, user_a.id)
    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "BAN_ALREADY_ACTIVE"


async def test_unban_allows_login(client, admin_token, user_a):
    await _ban(client, admin_token, user_a.id)
    await client.post(UNBAN_URL.format(user_id=user_a.id), headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})
    assert resp.status_code == 200


async def test_unban_no_active_ban_returns_404(client, admin_token, user_a):
    resp = await client.post(UNBAN_URL.format(user_id=user_a.id), headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 404


async def test_dept_admin_a_cannot_ban_user_in_dept_b(client, dept_admin_a_token, user_b):
    resp = await _ban(client, dept_admin_a_token, user_b.id)
    assert resp.status_code == 403


async def test_regular_user_cannot_ban(client, user_a_token, user_b):
    resp = await _ban(client, user_a_token, user_b.id)
    assert resp.status_code == 403


async def test_ban_nonexistent_user_returns_404(client, admin_token):
    resp = await _ban(client, admin_token, "usr_nonexistent")
    assert resp.status_code == 404


async def test_ban_revokes_active_sessions(client, admin_token, user_a):
    login_data = (await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})).json()
    await _ban(client, admin_token, user_a.id)
    resp = await client.post("/api/auth/v1/refresh", json={"refresh_token": login_data["refresh_token"]})
    assert resp.status_code in (401, 403)
