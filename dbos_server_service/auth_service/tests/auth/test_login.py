"""Тесты: POST /api/auth/v1/login — вход в систему."""

from datetime import datetime, timedelta, timezone

import pytest

from src.models import User
from src.utils.ids import _new_id
from src.core.security import hash_password

URL = "/api/auth/v1/login"


async def _login(client, username="t_admin", password="Admin12345678!"):
    return await client.post(URL, json={"username": username, "password": password})


# ── Success cases ─────────────────────────────────────────────────────────────

async def test_login_returns_tokens_and_identity(client, account_admin):
    resp = await _login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "Bearer"
    assert "identity" in body


async def test_login_identity_contains_required_fields(client, account_admin):
    body = (await _login(client)).json()
    identity = body["identity"]
    assert identity["user_id"] == account_admin.id
    assert identity["username"] == "t_admin"
    assert identity["platform_role"] == "account_admin"


async def test_login_account_admin_has_no_service_access(client, account_admin):
    body = (await _login(client)).json()
    identity = body["identity"]
    assert identity["allowed_services"] == []
    assert identity["service_roles"] == {}
    assert identity["department_id"] is None


async def test_login_regular_user_has_service_roles(client, user_a, service_x):
    """User whose dept has service access sees it in identity."""
    resp = await client.post(URL, json={"username": "t_user_a", "password": "User12345678!"})
    assert resp.status_code == 200
    identity = resp.json()["identity"]
    assert service_x.service_name in identity["allowed_services"]
    assert "reader" in identity["service_roles"].get(service_x.service_name, [])


async def test_login_jwt_payload_has_no_sensitive_claims(client, user_a, service_x):
    """JWT base64-decode'ится без ключа — `allowed_services`/`service_roles`
    в payload утекают права через любой логированный или перехваченный токен.
    Эти поля живые: introspect / get_current_identity revalidate'ят из БД,
    в payload им не место. Проверяем что их там нет.

    `username`/`department_id`/`platform_role` тоже выкинули — это про
    принадлежность к отделу/полу-публичный nameID, тоже не нужны в payload.
    """
    from src.core.security import decode_access_token

    resp = await client.post(URL, json={"username": "t_user_a", "password": "User12345678!"})
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    payload = decode_access_token(token)

    assert "allowed_services" not in payload
    assert "service_roles" not in payload
    # за компанию — остальные privilege-поля тоже не должны лежать в JWT
    assert "platform_role" not in payload
    assert "department_id" not in payload
    assert "username" not in payload

    # Минимальный набор обязан остаться — на нём держится диспатч revalidate.
    assert payload["sub"] == user_a.id
    assert payload["actor_type"] == "user"


async def test_login_jwt_payload_minimal_for_admin(client, account_admin):
    """Тот же чек для account_admin — у него тоже не должно быть privilege
    claims в JWT (revalidate всё пересоберёт)."""
    from src.core.security import decode_access_token

    resp = await _login(client)
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    payload = decode_access_token(token)

    assert "allowed_services" not in payload
    assert "service_roles" not in payload
    assert "platform_role" not in payload
    assert payload["sub"] == account_admin.id
    assert payload["actor_type"] == "user"


async def test_login_response_has_request_id_header(client, account_admin):
    resp = await _login(client)
    assert "x-request-id" in resp.headers


async def test_login_propagates_custom_request_id(client, account_admin):
    resp = await client.post(URL, json={"username": "t_admin", "password": "Admin12345678!"},
                              headers={"X-Request-ID": "test-req-001"})
    assert resp.headers["x-request-id"] == "test-req-001"


# ── Credential errors ─────────────────────────────────────────────────────────

async def test_login_wrong_password_returns_401(client, account_admin):
    resp = await _login(client, password="wrong")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_login_unknown_user_returns_401(client):
    resp = await _login(client, username="nobody", password="whatever")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_login_unknown_user_does_not_reveal_existence(client):
    """Error code must be identical for wrong user vs wrong password."""
    resp_no_user = await _login(client, username="ghost", password="x")
    resp_wrong_pw = await client.post(URL, json={"username": "test_admin_x", "password": "x"})
    assert resp_no_user.json()["error_code"] == "INVALID_CREDENTIALS"
    assert resp_wrong_pw.json()["error_code"] == "INVALID_CREDENTIALS"


# ── Account status ────────────────────────────────────────────────────────────

async def test_login_blocked_user_returns_403(client, db):
    user = User(
        id=_new_id("usr_"),
        username="blocked_user",
        password_hash=hash_password("Pass12345678!"),
        status="blocked",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    resp = await _login(client, username="blocked_user", password="Pass12345678!")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BLOCKED"


async def test_login_banned_user_returns_403(client, db):
    user = User(
        id=_new_id("usr_"),
        username="banned_user",
        password_hash=hash_password("Pass12345678!"),
        status="banned",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    resp = await _login(client, username="banned_user", password="Pass12345678!")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BANNED"


# ── Lockout ───────────────────────────────────────────────────────────────────

async def test_login_lockout_after_5_failed_attempts(client, db):
    user = User(
        id=_new_id("usr_"),
        username="lockout_target",
        password_hash=hash_password("Correct12345678!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    for _ in range(5):
        await client.post(URL, json={"username": "lockout_target", "password": "wrong"})

    resp = await _login(client, username="lockout_target", password="Correct12345678!")
    assert resp.status_code == 429
    assert resp.json()["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"
    assert "retry_after_seconds" in resp.json().get("details", {})


async def test_login_lockout_response_contains_retry_after(client, db):
    user = User(
        id=_new_id("usr_"),
        username="lockout_target2",
        password_hash=hash_password("Correct12345678!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    for _ in range(5):
        await client.post(URL, json={"username": "lockout_target2", "password": "wrong"})

    resp = await _login(client, username="lockout_target2", password="Correct12345678!")
    assert resp.json()["details"]["retry_after_seconds"] > 0


async def test_login_succeeds_after_lockout_expires(client, db):
    """If locked_until is in the past, login should succeed."""
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    user = User(
        id=_new_id("usr_"),
        username="expired_lockout",
        password_hash=hash_password("Correct12345678!"),
        status="active",
        is_active=True,
        failed_login_attempts=5,
        locked_until=past,
    )
    db.add(user)
    await db.flush()

    resp = await _login(client, username="expired_lockout", password="Correct12345678!")
    assert resp.status_code == 200


async def test_login_failed_attempts_persist_in_db(client, db):
    """Regression: counter must be committed before raise, not rolled back.

    Before the lockout-commit fix, ``get_db()`` rolled back the increment after
    ``raise AuthenticationError`` and ``failed_login_attempts`` stayed at 0 in
    the database. We re-read straight from the DB (bypassing the ORM identity
    map) to assert the value was actually persisted.
    """
    from sqlalchemy import select as sa_select
    from src.models import User as UserModel

    user = User(
        id=_new_id("usr_"),
        username="persist_target",
        password_hash=hash_password("Correct12345678!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    user_id_val = user.id

    # 3 wrong-password attempts — must persist
    for _ in range(3):
        await client.post(URL, json={"username": "persist_target", "password": "wrong"})

    db.expire_all()
    fresh = await db.scalar(sa_select(UserModel).where(UserModel.id == user_id_val))
    assert fresh.failed_login_attempts == 3, (
        f"counter was not committed before raise: got {fresh.failed_login_attempts}, expected 3"
    )
    assert fresh.locked_until is None


async def test_login_lockout_persists_locked_until_in_db(client, db):
    """Regression: at 5th failed attempt, locked_until must be persisted, not rolled back."""
    from sqlalchemy import select as sa_select
    from src.models import User as UserModel

    user = User(
        id=_new_id("usr_"),
        username="lock_persist_target",
        password_hash=hash_password("Correct12345678!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    user_id_val = user.id

    for _ in range(5):
        await client.post(URL, json={"username": "lock_persist_target", "password": "wrong"})

    db.expire_all()
    fresh = await db.scalar(sa_select(UserModel).where(UserModel.id == user_id_val))
    assert fresh.failed_login_attempts == 5
    assert fresh.locked_until is not None, "locked_until must be persisted at lockout threshold"
    assert fresh.locked_until > datetime.now(timezone.utc)


async def test_login_reset_after_expired_lockout_persists(client, db):
    """Regression: after expired-lockout reset, the zeroed counter must be committed.

    If the reset is not committed, a subsequent failed login would re-read a
    stale counter and could re-lock the user immediately.
    """
    from sqlalchemy import select as sa_select
    from src.models import User as UserModel

    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    user = User(
        id=_new_id("usr_"),
        username="reset_persist_target",
        password_hash=hash_password("Correct12345678!"),
        status="active",
        is_active=True,
        failed_login_attempts=5,
        locked_until=past,
    )
    db.add(user)
    await db.flush()
    user_id_val = user.id

    # Wrong password — triggers the expired-lockout reset branch, then increments.
    await client.post(URL, json={"username": "reset_persist_target", "password": "wrong"})

    db.expire_all()
    fresh = await db.scalar(sa_select(UserModel).where(UserModel.id == user_id_val))
    # After expired-lockout reset (counter=0, locked_until=None) and one wrong
    # password, counter must be exactly 1 — proving both the reset and the
    # increment were persisted.
    assert fresh.failed_login_attempts == 1, (
        f"reset+increment not persisted: got {fresh.failed_login_attempts}, expected 1"
    )
    assert fresh.locked_until is None


# ── Error format ──────────────────────────────────────────────────────────────

async def test_login_error_contains_required_fields(client, account_admin):
    resp = await _login(client, password="bad")
    body = resp.json()
    for field in ("error", "error_code", "message", "request_id", "timestamp"):
        assert field in body, f"missing field: {field}"


async def test_login_missing_password_returns_422(client):
    resp = await client.post(URL, json={"username": "admin"})
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "VALIDATION_ERROR"
