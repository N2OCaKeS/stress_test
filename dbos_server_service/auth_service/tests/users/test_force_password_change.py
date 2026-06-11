"""Тесты: POST /api/auth/v1/users/{id}/force-password-change."""

import pytest

URL_TPL = "/api/auth/v1/users/{user_id}/force-password-change"
ME_URL = "/api/auth/v1/me"
LOGIN_URL = "/api/auth/v1/login"


async def _login(client, username, password):
    resp = await client.post(LOGIN_URL, json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_account_admin_forces_target(client, admin_token, db, user_a):
    """account_admin → любой target → 200, флаг True, audit эмитится."""
    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    await db.refresh(user_a)
    assert user_a.must_change_password is True


async def test_dept_admin_forces_own_dept(client, dept_admin_a_token, db, user_a):
    """dep_admin своего отдела → 200."""
    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 200
    await db.refresh(user_a)
    assert user_a.must_change_password is True


async def test_dept_admin_cross_dept_returns_403(client, dept_admin_a_token, db, user_b):
    """dep_admin → юзер чужого отдела → 403 DEPT_MISMATCH."""
    resp = await client.post(
        URL_TPL.format(user_id=user_b.id),
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "DEPT_MISMATCH"
    await db.refresh(user_b)
    assert user_b.must_change_password is False


async def test_regular_user_returns_403(client, user_a_token, user_b):
    """Обычный юзер → 403 ROLE_REQUIRED (от AnyAdmin-guard'а)."""
    resp = await client.post(
        URL_TPL.format(user_id=user_b.id),
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ROLE_REQUIRED"


async def test_unknown_user_returns_404(client, admin_token):
    """Не существующий target → 404 USER_NOT_FOUND."""
    resp = await client.post(
        URL_TPL.format(user_id="usr_nonexistent"),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "USER_NOT_FOUND"


async def test_self_force_is_allowed(client, admin_token, db, account_admin):
    """Сам себе ставить флаг разрешено (полезно для проверки flow)."""
    resp = await client.post(
        URL_TPL.format(user_id=account_admin.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    await db.refresh(account_admin)
    assert account_admin.must_change_password is True


async def test_after_force_target_blocked_on_non_whitelist(client, admin_token, user_a):
    """Главное: после флага target → /me → 403 PASSWORD_CHANGE_REQUIRED.

    Это самый важный кейс — он проверяет, что middleware видит флаг
    немедленно (identity-cache сброшен) и режет target'а до self-reset'а.
    """
    # Сначала target логинится — токен валиден, флага ещё нет
    target_token = await _login(client, "t_user_a", "User12345678!")
    pre = await client.get(ME_URL, headers={"Authorization": f"Bearer {target_token}"})
    assert pre.status_code == 200

    # Admin ставит флаг
    flip = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert flip.status_code == 200

    # Target теперь упирается в middleware
    post = await client.get(
        ME_URL, headers={"Authorization": f"Bearer {target_token}"},
    )
    assert post.status_code == 403, post.text
    assert post.json()["error_code"] == "PASSWORD_CHANGE_REQUIRED"


async def test_emits_audit(client, admin_token, user_a, capture_audit_payloads):
    """Audit action `user.force_password_change` эмитится."""
    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    actions = [p.get("action") for p in capture_audit_payloads]
    assert "user.force_password_change" in actions


async def test_identity_cache_invalidated(client, admin_token, user_a, monkeypatch):
    """Identity-cache target'а сбрасывается сразу, не по TTL."""
    invalidated: list[str] = []

    def _spy(uid):
        invalidated.append(uid)

    monkeypatch.setattr(
        "src.services.user_service._invalidate_identity_cache", _spy
    )

    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert user_a.id in invalidated, (
        f"identity-cache target'а должен быть сброшен, got {invalidated}"
    )


async def test_no_auth_returns_401(client, user_a):
    """Без Bearer → 401."""
    resp = await client.post(URL_TPL.format(user_id=user_a.id))
    assert resp.status_code == 401
