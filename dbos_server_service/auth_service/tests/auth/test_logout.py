"""Тесты: POST /api/auth/v1/logout — выход из системы."""

URL = "/api/auth/v1/logout"


async def _login(client, username="t_admin", password="Admin1234!"):
    r = await client.post("/api/auth/v1/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()


async def test_logout_revokes_refresh_token(client, account_admin):
    data = await _login(client)
    resp = await client.post(URL, json={"refresh_token": data["refresh_token"]})
    assert resp.status_code == 200
    r = await client.post("/api/auth/v1/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 401


async def test_logout_invalid_token_returns_200(client, account_admin):
    """Logout is idempotent and doesn't reveal token validity."""
    resp = await client.post(URL, json={"refresh_token": "nonexistent_token_abc"})
    assert resp.status_code == 200


async def test_logout_twice_same_token_returns_200(client, account_admin):
    data = await _login(client)
    await client.post(URL, json={"refresh_token": data["refresh_token"]})
    resp = await client.post(URL, json={"refresh_token": data["refresh_token"]})
    assert resp.status_code == 200


async def test_logout_does_not_affect_other_sessions(client, account_admin):
    s1 = await _login(client)
    s2 = await _login(client)
    await client.post(URL, json={"refresh_token": s1["refresh_token"]})
    r = await client.post("/api/auth/v1/refresh", json={"refresh_token": s2["refresh_token"]})
    assert r.status_code == 200


async def test_logout_missing_token_returns_422(client, account_admin):
    resp = await client.post(URL, json={})
    assert resp.status_code == 422
