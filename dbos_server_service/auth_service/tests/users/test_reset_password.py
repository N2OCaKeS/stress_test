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


# ── Cross-department escalation ───────────────────────────────────────────
#
# Раньше endpoint `POST /users/{id}/reset-password` имел `AnyAdmin` guard,
# но `services/user_service.reset_password` не сравнивал `actor.department_id`
# с `target.department_id`. department_admin отдела A мог сбрасывать пароли
# юзерам отдела B → privilege escalation.
# Фикс: явная department-проверка для `DEPARTMENT_ADMIN`-actor'а.

async def test_dept_admin_cannot_reset_password_cross_department(
    client, dept_admin_a_token, user_b,
):
    """dept_admin_a → reset password юзера dep_b → 403 DEPARTMENT_ISOLATION."""
    resp = await client.post(
        URL_TPL.format(user_id=user_b.id),
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"new_password": "Hacked1234!"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"


async def test_dept_admin_can_reset_password_within_department(
    client, dept_admin_a_token, user_a,
):
    """dept_admin_a → reset password юзера dep_a → 200 (own department)."""
    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"new_password": "NewPass1234!"},
    )
    assert resp.status_code == 200


async def test_account_admin_can_reset_any_user(client, admin_token, user_b):
    """account_admin сбрасывает пароль кросс-dept-юзеру → 200.

    Регрессия-страховка: фикс DEPARTMENT_ISOLATION не должен отбивать
    account_admin'а, у которого `department_id is None` и `platform_role`
    отличается от DEPARTMENT_ADMIN.
    """
    resp = await client.post(
        URL_TPL.format(user_id=user_b.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_password": "NewPass1234!"},
    )
    assert resp.status_code == 200
