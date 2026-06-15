"""Тесты: POST /api/auth/v1/login — HttpOnly refresh cookie.

`dbos_refresh` ставится после успешного логина с флагами HttpOnly + SameSite=Strict
+ Path=/api/auth/v1. Тело ответа `refresh_token` остаётся (backward-compat).
"""

URL = "/api/auth/v1/login"


async def test_login_sets_refresh_cookie(client, account_admin):
    resp = await client.post(URL, json={"username": "t_admin", "password": "Admin12345678!"})
    assert resp.status_code == 200

    raw_set_cookie = resp.headers.get("set-cookie", "")
    assert "dbos_refresh=" in raw_set_cookie
    assert "HttpOnly" in raw_set_cookie
    # httpx нормализует регистр attribute'ов, но SameSite/Path всё равно ловятся.
    assert "samesite=strict" in raw_set_cookie.lower()
    assert "path=/api/auth/v1" in raw_set_cookie.lower()


async def test_login_cookie_value_matches_body_refresh_token(client, account_admin):
    resp = await client.post(URL, json={"username": "t_admin", "password": "Admin12345678!"})
    assert resp.status_code == 200
    body_refresh = resp.json()["refresh_token"]
    cookie_refresh = resp.cookies.get("dbos_refresh")
    assert cookie_refresh == body_refresh


async def test_login_response_body_still_contains_refresh_token(client, account_admin):
    """Старые клиенты, которые читают refresh из body, не ломаем."""
    resp = await client.post(URL, json={"username": "t_admin", "password": "Admin12345678!"})
    body = resp.json()
    assert isinstance(body.get("refresh_token"), str)
    assert len(body["refresh_token"]) > 0
