"""Тесты: POST /api/auth/v1/users/{id}/reset-password — сброс пароля пользователя."""

URL_TPL = "/api/auth/v1/users/{user_id}/reset-password"
LOGIN_URL = "/api/auth/v1/login"


async def test_admin_resets_password(client, admin_token, user_a):
    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_password": "NewPass1234!"},
    )
    assert resp.status_code == 200


async def test_new_password_works_for_login(client, admin_token, user_a):
    await client.post(URL_TPL.format(user_id=user_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"new_password": "NewPass1234!"})
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "NewPass1234!"})
    assert resp.status_code == 200


async def test_old_password_no_longer_works(client, admin_token, user_a):
    await client.post(URL_TPL.format(user_id=user_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"new_password": "NewPass1234!"})
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})
    assert resp.status_code == 401


async def test_reset_revokes_active_sessions(client, admin_token, user_a):
    login_data = (await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})).json()
    await client.post(URL_TPL.format(user_id=user_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"new_password": "NewPass1234!"})
    resp = await client.post("/api/auth/v1/refresh", json={"refresh_token": login_data["refresh_token"]})
    assert resp.status_code == 401


async def test_regular_user_cannot_reset_password(client, user_a_token, user_b):
    resp = await client.post(URL_TPL.format(user_id=user_b.id),
                              headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"new_password": "Hacked1234!"})
    assert resp.status_code == 403


async def test_reset_nonexistent_user_returns_404(client, admin_token):
    resp = await client.post(URL_TPL.format(user_id="usr_nonexistent"),
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"new_password": "Pass1234!"})
    assert resp.status_code == 404
