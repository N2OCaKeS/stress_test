"""Тесты: POST /api/auth/v1/logout — cookie fallback и очистка cookie."""

from tests._helpers.http import login as _login

URL = "/api/auth/v1/logout"


async def test_logout_uses_cookie_when_body_empty(client, account_admin):
    data = await _login(client)
    resp = await client.post(URL, json={})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Refresh должен быть инвалидирован.
    r = await client.post("/api/auth/v1/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 401


async def test_logout_clears_refresh_cookie(client, account_admin):
    await _login(client)
    resp = await client.post(URL, json={})
    assert resp.status_code == 200

    raw_set_cookie = resp.headers.get("set-cookie", "")
    # delete_cookie выставляет Max-Age=0 или expires в прошлое.
    assert "dbos_refresh=" in raw_set_cookie
    lowered = raw_set_cookie.lower()
    assert ("max-age=0" in lowered) or ("expires=" in lowered)
    assert "path=/api/auth/v1" in lowered


async def test_logout_missing_token_is_idempotent(client, account_admin):
    """Cookie не лежит и body пустой → 200, юзер уже de-facto разлогинен.

    Раньше эта ветка падала 422 MISSING_REFRESH_TOKEN — UI получал ошибку
    при повторном logout / logout без cookie. Сейчас сервер просто чистит
    cookie и возвращает OkResponse.
    """
    client.cookies.clear()
    resp = await client.post(URL, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


async def test_logout_body_takes_precedence_over_cookie(client, account_admin):
    """Body имеет приоритет над cookie."""
    s1 = await _login(client)
    s2 = await _login(client)
    # Cookie сейчас от s2; передаём в body s1 — отзовётся именно s1.
    resp = await client.post(URL, json={"refresh_token": s1["refresh_token"]})
    assert resp.status_code == 200

    # s2 всё ещё живой (его не трогали).
    r = await client.post("/api/auth/v1/refresh", json={"refresh_token": s2["refresh_token"]})
    assert r.status_code == 200
