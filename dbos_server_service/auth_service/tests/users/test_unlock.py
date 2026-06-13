"""Тесты: POST /api/auth/v1/users/{id}/unlock — admin снимает brute-force lockout.

Lockout (5 неудач → ~15 минут) штатно сбрасывается только успешным логином
или истечением окна. Этот endpoint даёт админу снять его явно. Authz —
симметрично ban/unban: account_admin любой target, department_admin только
свой отдел (иначе 403 DEPT_MISMATCH), обычный юзер — 403 ROLE_REQUIRED.
"""

from src.services.audit_events import SERVICE_EVENTS

LOGIN_URL = "/api/auth/v1/login"
UNLOCK_URL = "/api/auth/v1/users/{user_id}/unlock"


def test_unlock_event_severity_is_critical():
    """`user.unlock` объявлен в каталоге с severity CRITICAL.

    Снятие brute-force lockout'а админом — security-sensitive действие
    (открывает повторный вход на атакуемый аккаунт), поэтому идёт в SIEM
    наравне с ban/unban/password_reset.
    """
    ev = next(e for e in SERVICE_EVENTS if e["action"] == "user.unlock")
    assert ev["default_severity"] == "CRITICAL"


async def _login(client, username, password):
    return await client.post(LOGIN_URL, json={"username": username, "password": password})


async def _lock_out(client, username):
    """Довести юзера до lockout'а пятью неудачными логинами."""
    for _ in range(5):
        await client.post(LOGIN_URL, json={"username": username, "password": "wrong"})


async def _unlock(client, token, user_id):
    return await client.post(
        UNLOCK_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
    )


# ── happy path ────────────────────────────────────────────────────────────────

async def test_unlock_locked_user_clears_state(client, admin_token, user_a, db):
    """Залоченного разлочиваем → 200, счётчик и locked_until обнулены."""
    await _lock_out(client, "t_user_a")
    await db.refresh(user_a)
    assert user_a.locked_until is not None
    assert user_a.failed_login_attempts >= 5

    resp = await _unlock(client, admin_token, user_a.id)
    assert resp.status_code == 200, resp.text

    await db.refresh(user_a)
    assert user_a.locked_until is None
    assert user_a.failed_login_attempts == 0


async def test_unlock_lets_user_login_again(client, admin_token, user_a):
    """После unlock'а юзер логинится сразу, не дожидаясь истечения окна."""
    await _lock_out(client, "t_user_a")
    # Залочен — даже верный пароль отбивается 429.
    locked = await _login(client, "t_user_a", "User12345678!")
    assert locked.status_code == 429
    assert locked.json()["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"

    assert (await _unlock(client, admin_token, user_a.id)).status_code == 200

    ok = await _login(client, "t_user_a", "User12345678!")
    assert ok.status_code == 200, ok.text


async def test_unlock_not_locked_is_noop(client, admin_token, user_a, db):
    """Разлочить незалоченного → 200, состояние не меняется (idempotent)."""
    resp = await _unlock(client, admin_token, user_a.id)
    assert resp.status_code == 200, resp.text
    await db.refresh(user_a)
    assert user_a.locked_until is None
    assert user_a.failed_login_attempts == 0


async def test_unlock_unknown_user_404(client, admin_token):
    resp = await _unlock(client, admin_token, "usr_does_not_exist")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error_code"] == "USER_NOT_FOUND"


# ── authz ────────────────────────────────────────────────────────────────────

async def test_dep_admin_unlocks_own_dept_user(client, dept_admin_a_token, user_a, db):
    """dep_admin снимает lockout с юзера своего отдела → 200."""
    await _lock_out(client, "t_user_a")
    resp = await _unlock(client, dept_admin_a_token, user_a.id)
    assert resp.status_code == 200, resp.text
    await db.refresh(user_a)
    assert user_a.locked_until is None


async def test_dep_admin_cross_dept_unlock_403(client, dept_admin_a_token, user_b):
    """dep_admin по юзеру чужого отдела → 403 DEPT_MISMATCH."""
    resp = await _unlock(client, dept_admin_a_token, user_b.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "DEPT_MISMATCH"


async def test_dep_admin_cannot_unlock_platform_admin(client, dept_admin_a_token, account_admin):
    """dep_admin по платформенному юзеру (без department_id) → 403."""
    resp = await _unlock(client, dept_admin_a_token, account_admin.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "DEPT_MISMATCH"


async def test_account_admin_unlocks_any_user(client, admin_token, user_b, db):
    """account_admin — любой отдел (regression)."""
    resp = await _unlock(client, admin_token, user_b.id)
    assert resp.status_code == 200, resp.text


async def test_regular_user_cannot_unlock(client, user_a_token, user_b):
    """Обычный юзер → 403 ROLE_REQUIRED (AnyAdmin-guard)."""
    resp = await _unlock(client, user_a_token, user_b.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_REQUIRED"
