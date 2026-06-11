"""Тесты: POST /api/auth/v1/logout — выход из системы."""

from datetime import datetime, timedelta, timezone

from src.core.security import hash_password, hash_refresh_token
from src.models import Session, User
from src.utils.ids import _new_id, session_id
from tests._helpers.http import login as _login  # noqa: F401 — общий helper

URL = "/api/auth/v1/logout"


async def test_logout_revokes_refresh_token(client, account_admin):
    data = await _login(client)
    resp = await client.post(URL, json={"refresh_token": data["refresh_token"]})
    assert resp.status_code == 200
    # Envelope: единый ключ revoked=True у endpoint'а logout.
    assert resp.json() == {"ok": True}
    r = await client.post("/api/auth/v1/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 401


async def test_logout_invalid_token_returns_200(client, account_admin):
    """Logout is idempotent and doesn't reveal token validity."""
    resp = await client.post(URL, json={"refresh_token": "nonexistent_token_abc"})
    assert resp.status_code == 200
    # Idempotent: envelope тот же, чтобы клиент не различал «было/не было».
    assert resp.json() == {"ok": True}


async def test_logout_twice_same_token_returns_200(client, account_admin):
    data = await _login(client)
    await client.post(URL, json={"refresh_token": data["refresh_token"]})
    resp = await client.post(URL, json={"refresh_token": data["refresh_token"]})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_logout_does_not_affect_other_sessions(client, account_admin):
    s1 = await _login(client)
    s2 = await _login(client)
    await client.post(URL, json={"refresh_token": s1["refresh_token"]})
    r = await client.post("/api/auth/v1/refresh", json={"refresh_token": s2["refresh_token"]})
    assert r.status_code == 200


async def test_logout_missing_token_is_idempotent(client, account_admin):
    """Без body и без cookie — юзер уже фактически разлогинен, возвращаем OK."""
    client.cookies.clear()
    resp = await client.post(URL, json={})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_logout_banned_user_active_session_returns_200(client, db):
    """Logout не зависит от is_banned: пользователь забанен после выдачи refresh,
    но logout по тому refresh-токену всё равно идемпотентен и закрывает сессию.
    """
    user = User(
        id=_new_id("usr_"),
        username="logout_banned",
        password_hash=hash_password("Pass12345678!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    data = await _login(client, username="logout_banned", password="Pass12345678!")
    # Жесткий бан после получения refresh — БД-стейт меняем напрямую.
    user.status = "banned"
    await db.flush()
    resp = await client.post(URL, json={"refresh_token": data["refresh_token"]})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_logout_expired_session_returns_200_idempotent(client, db):
    """Refresh уже истёк (expires_at в прошлом), но сессия ещё is_active=True.
    Logout всё равно возвращает 200 — endpoint не различает live/expired сессии
    в ответе, чтобы не палить состояние токена.
    """
    user = User(
        id=_new_id("usr_"),
        username="logout_expired",
        password_hash=hash_password("Pass12345678!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    raw_refresh = "expired_raw_secret_for_logout_test"
    sess = Session(
        id=session_id(),
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(raw_refresh),
        is_active=True,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(sess)
    await db.flush()
    resp = await client.post(URL, json={"refresh_token": raw_refresh})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
