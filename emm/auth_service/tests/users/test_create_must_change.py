"""Тесты: `must_change_password` как поле тела `POST /api/auth/v1/users`.

Поле теперь часть create-body — UI не делает второй вызов force-password-change.

Покрывает:
  * account_admin создаёт с `must_change_password=false` → юзер сразу проходит
    на gated endpoint (`/me` 200, force-change не висит).
  * department_admin с `false` → 403 CANNOT_BYPASS_PASSWORD_CHANGE (dep_admin
    не может обойти force-change).
  * без поля → дефолт True (юзер блокируется force-change'ем).
  * account_admin с `true` → True (явный force-change).
"""

URL = "/api/auth/v1/users"
LOGIN_URL = "/api/auth/v1/login"
ME_URL = "/api/auth/v1/me"


async def _login(client, username, password):
    resp = await client.post(LOGIN_URL, json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_account_admin_create_bypass_no_force(client, admin_token, dept_a_with_service):
    """account_admin с must_change_password=false → новый юзер сразу /me 200."""
    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "no_force_user",
            "password": "Temp12345678!",
            "department_id": dept_a_with_service.id,
            "must_change_password": False,
        },
    )
    assert resp.status_code == 201, resp.text

    token = await _login(client, "no_force_user", "Temp12345678!")
    me = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text


async def test_dept_admin_bypass_forbidden(client, dept_admin_a_token, dept_a_with_service):
    """department_admin с must_change_password=false → 403 CANNOT_BYPASS_PASSWORD_CHANGE."""
    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={
            "username": "da_bypass_user",
            "password": "Temp12345678!",
            "department_id": dept_a_with_service.id,
            "must_change_password": False,
        },
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "CANNOT_BYPASS_PASSWORD_CHANGE"


async def test_create_without_field_defaults_force(client, admin_token, dept_a_with_service):
    """Без поля → дефолт True: новый юзер блокируется force-change'ем."""
    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "default_force_user",
            "password": "Temp12345678!",
            "department_id": dept_a_with_service.id,
        },
    )
    assert resp.status_code == 201, resp.text

    token = await _login(client, "default_force_user", "Temp12345678!")
    blocked = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 403
    assert blocked.json()["error_code"] == "PASSWORD_CHANGE_REQUIRED"


async def test_account_admin_explicit_true_forces(client, admin_token, dept_a_with_service):
    """account_admin с must_change_password=true → флаг True (блокировка force-change)."""
    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "explicit_force_user",
            "password": "Temp12345678!",
            "department_id": dept_a_with_service.id,
            "must_change_password": True,
        },
    )
    assert resp.status_code == 201, resp.text

    token = await _login(client, "explicit_force_user", "Temp12345678!")
    blocked = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 403
    assert blocked.json()["error_code"] == "PASSWORD_CHANGE_REQUIRED"
