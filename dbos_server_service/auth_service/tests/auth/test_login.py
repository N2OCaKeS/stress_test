"""Тесты: POST /api/auth/v1/login — вход в систему."""

from datetime import datetime, timedelta, timezone

import pytest

from src.models import User
from src.utils.ids import _new_id
from src.core.security import hash_password

URL = "/api/auth/v1/login"


async def _login(client, username="t_admin", password="Admin1234!"):
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
    resp = await client.post(URL, json={"username": "t_user_a", "password": "User1234!"})
    assert resp.status_code == 200
    identity = resp.json()["identity"]
    assert service_x.service_name in identity["allowed_services"]
    assert "reader" in identity["service_roles"].get(service_x.service_name, [])


async def test_login_response_has_request_id_header(client, account_admin):
    resp = await _login(client)
    assert "x-request-id" in resp.headers


async def test_login_propagates_custom_request_id(client, account_admin):
    resp = await client.post(URL, json={"username": "t_admin", "password": "Admin1234!"},
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
        password_hash=hash_password("Pass1234!"),
        status="blocked",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    resp = await _login(client, username="blocked_user", password="Pass1234!")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BLOCKED"


async def test_login_banned_user_returns_403(client, db):
    user = User(
        id=_new_id("usr_"),
        username="banned_user",
        password_hash=hash_password("Pass1234!"),
        status="banned",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    resp = await _login(client, username="banned_user", password="Pass1234!")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BANNED"


# ── Lockout ───────────────────────────────────────────────────────────────────

async def test_login_lockout_after_5_failed_attempts(client, db):
    user = User(
        id=_new_id("usr_"),
        username="lockout_target",
        password_hash=hash_password("Correct1!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    for _ in range(5):
        await client.post(URL, json={"username": "lockout_target", "password": "wrong"})

    resp = await _login(client, username="lockout_target", password="Correct1!")
    assert resp.status_code == 429
    assert resp.json()["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"
    assert "retry_after_seconds" in resp.json().get("details", {})


async def test_login_lockout_response_contains_retry_after(client, db):
    user = User(
        id=_new_id("usr_"),
        username="lockout_target2",
        password_hash=hash_password("Correct1!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    for _ in range(5):
        await client.post(URL, json={"username": "lockout_target2", "password": "wrong"})

    resp = await _login(client, username="lockout_target2", password="Correct1!")
    assert resp.json()["details"]["retry_after_seconds"] > 0


async def test_login_succeeds_after_lockout_expires(client, db):
    """If locked_until is in the past, login should succeed."""
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    user = User(
        id=_new_id("usr_"),
        username="expired_lockout",
        password_hash=hash_password("Correct1!"),
        status="active",
        is_active=True,
        failed_login_attempts=5,
        locked_until=past,
    )
    db.add(user)
    await db.flush()

    resp = await _login(client, username="expired_lockout", password="Correct1!")
    assert resp.status_code == 200


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
