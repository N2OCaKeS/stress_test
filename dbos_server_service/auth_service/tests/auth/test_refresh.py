"""Тесты: POST /api/auth/v1/refresh — обновление access-токена."""

URL = "/api/auth/v1/refresh"


async def _login(client, username="t_admin", password="Admin1234!"):
    r = await client.post("/api/auth/v1/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()


async def test_refresh_returns_new_tokens(client, account_admin):
    data = await _login(client)
    resp = await client.post(URL, json={"refresh_token": data["refresh_token"]})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["refresh_token"] != data["refresh_token"]


async def test_refresh_old_token_invalid_after_rotation(client, account_admin):
    data = await _login(client)
    old_rt = data["refresh_token"]
    await client.post(URL, json={"refresh_token": old_rt})
    resp = await client.post(URL, json={"refresh_token": old_rt})
    assert resp.status_code == 401


async def test_refresh_reuse_revokes_all_sessions(client, account_admin):
    """Using an already-rotated token should revoke ALL sessions (reuse detection)."""
    s1 = await _login(client)
    s2 = await _login(client)
    old_rt = s1["refresh_token"]
    await client.post(URL, json={"refresh_token": old_rt})  # rotate once
    await client.post(URL, json={"refresh_token": old_rt})  # reuse → triggers revocation
    resp = await client.post(URL, json={"refresh_token": s2["refresh_token"]})
    assert resp.status_code == 401


async def test_refresh_expired_token_returns_401(client, account_admin):
    resp = await client.post(URL, json={"refresh_token": "completely_fake_token"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] in ("REFRESH_TOKEN_INVALID", "REFRESH_TOKEN_EXPIRED")
