"""Тесты: POST /api/auth/v1/refresh — обновление access-токена."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from src.core.security import hash_refresh_token
from src.models import Session

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


async def test_reuse_of_token_three_rotations_ago_detected(client, account_admin):
    """RT, ротированный 3 раза назад, всё ещё ловится — sliding window N>=3."""
    data = await _login(client)
    very_old = data["refresh_token"]

    # 3 успешные ротации поверх very_old.
    rt = very_old
    for _ in range(3):
        resp = await client.post(URL, json={"refresh_token": rt})
        assert resp.status_code == 200
        rt = resp.json()["refresh_token"]

    # Подсовываем самый старый RT — должно сработать reuse-detection.
    resp = await client.post(URL, json={"refresh_token": very_old})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "REFRESH_TOKEN_INVALID"

    # И живой текущий RT тоже должен быть отозван (revoke_all_for_user).
    resp = await client.post(URL, json={"refresh_token": rt})
    assert resp.status_code == 401


async def test_reuse_of_token_five_rotations_ago_detected(client, account_admin):
    """N=5: граничный случай — window ровно в 5 поколений."""
    data = await _login(client)
    very_old = data["refresh_token"]

    rt = very_old
    for _ in range(5):
        resp = await client.post(URL, json={"refresh_token": rt})
        assert resp.status_code == 200
        rt = resp.json()["refresh_token"]

    resp = await client.post(URL, json={"refresh_token": very_old})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "REFRESH_TOKEN_INVALID"


async def test_refresh_truly_expired_session_returns_expired_error(client, account_admin, db):
    """Истёкший по времени refresh_token (Session.expires_at в прошлом) → 401 REFRESH_TOKEN_EXPIRED."""
    data = await _login(client)
    raw = data["refresh_token"]

    await db.execute(
        update(Session)
        .where(Session.refresh_token_hash == hash_refresh_token(raw))
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=10))
    )
    await db.commit()

    resp = await client.post(URL, json={"refresh_token": raw})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "REFRESH_TOKEN_EXPIRED"
