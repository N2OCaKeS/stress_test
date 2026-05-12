"""Тесты: GET /api/auth/v1/me — текущий пользователь."""

from datetime import timedelta

from src.core.security import create_access_token

URL = "/api/auth/v1/me"


async def test_me_returns_correct_identity(client, user_a, user_a_token, service_x):
    resp = await client.get(URL, headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == user_a.id
    assert body["username"] == "t_user_a"
    assert service_x.service_name in body["allowed_services"]


async def test_me_account_admin_has_no_services(client, account_admin, admin_token):
    resp = await client.get(URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed_services"] == []
    assert body["service_roles"] == {}
    assert body["department_id"] is None


async def test_me_reflects_current_db_state(client, user_a, user_a_token, dept_a_with_service, service_x, db):
    """me endpoint re-reads DB, not just the JWT payload."""
    resp = await client.get(URL, headers={"Authorization": f"Bearer {user_a_token}"})
    assert service_x.service_name in resp.json()["allowed_services"]


async def test_me_without_token_returns_401(client, account_admin):
    resp = await client.get(URL)
    assert resp.status_code == 401


async def test_me_with_invalid_token_returns_401(client, account_admin):
    resp = await client.get(URL, headers={"Authorization": "Bearer invalid.jwt.token"})
    assert resp.status_code == 401


async def test_me_with_truly_expired_jwt_returns_401(client, user_a):
    """JWT с exp в прошлом, но валидной подписью → 401 ACCESS_TOKEN_EXPIRED (плоский envelope)."""
    expired = create_access_token(
        {"sub": user_a.id, "username": user_a.username},
        expires_delta=timedelta(seconds=-1),
    )
    resp = await client.get(URL, headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "ACCESS_TOKEN_EXPIRED"


async def test_me_department_admin_has_dept_id(client, dept_admin_a, dept_admin_a_token, dept_a):
    resp = await client.get(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"})
    assert resp.status_code == 200
    assert resp.json()["department_id"] == dept_a.id
