"""Тесты: POST /api/auth/v1/users/me/password — self-reset пароля с old_password-confirm."""

from src.core.config import get_settings
from datetime import timedelta
from src.utils.time import utcnow

URL = "/api/auth/v1/users/me/password"
LOGIN_URL = "/api/auth/v1/login"
TOKENS_URL = "/api/auth/v1/tokens"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
REFRESH_URL = "/api/auth/v1/refresh"


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_user_changes_own_password(client, user_a_token):
    """Self-reset с правильным old_password → 200, новый пароль работает."""
    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "User12345678!", "new_password": "NewSecret9012345!"},
    )
    assert resp.status_code == 200, resp.text

    login_old = await client.post(
        LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"}
    )
    assert login_old.status_code == 401

    login_new = await client.post(
        LOGIN_URL, json={"username": "t_user_a", "password": "NewSecret9012345!"}
    )
    assert login_new.status_code == 200


# ── Wrong old password → 401 + lockout ──────────────────────────────────────


async def test_wrong_old_password_returns_invalid_old_password(client, user_a_token):
    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "WrongOld1!", "new_password": "NewSecret9012345!"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_OLD_PASSWORD"


async def test_wrong_old_password_locks_account_after_threshold(client, user_a_token):
    """N неудачных попыток подтвердить старый пароль → 429 ACCOUNT_TEMPORARILY_LOCKED.

    Делит счётчик с /login: после блокировки и /login тем же юзером отбивается
    429-кой, brute-force смены пароля не обходит lockout логина.
    """
    settings = get_settings()
    last_status = None
    for _ in range(settings.max_failed_login_attempts):
        last_resp = await client.post(
            URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"old_password": "WrongOld1!", "new_password": "NewSecret9012345!"},
        )
        last_status = last_resp.status_code
    # Последняя попытка либо 401 (последний промах до блокировки), либо уже 429.
    assert last_status in (401, 429)

    # Следующая попытка — гарантированно 429.
    locked = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "User12345678!", "new_password": "NewSecret9012345!"},
    )
    assert locked.status_code == 429
    assert locked.json()["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"


# ── Password policy enforced ────────────────────────────────────────────────


async def test_new_password_must_satisfy_policy(client, user_a_token):
    """new_password без цифр → 422 (Pydantic policy validator)."""
    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "User12345678!", "new_password": "onlyletters"},
    )
    assert resp.status_code == 422


async def test_new_password_same_as_old_returns_422(client, user_a_token):
    """Новый пароль == старый → 422 SAME_PASSWORD."""
    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "User12345678!", "new_password": "User12345678!"},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "SAME_PASSWORD"


# ── Sessions and PATs revoked on self-reset ─────────────────────────────────


async def test_self_reset_revokes_active_refresh(client, user_a):
    """После смены пароля refresh-токен старой сессии не работает."""
    login_data = (
        await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"}
        )
    ).json()
    access = login_data["access_token"]
    refresh = login_data["refresh_token"]

    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {access}"},
        json={"old_password": "User12345678!", "new_password": "NewSecret9012345!"},
    )
    assert resp.status_code == 200

    refresh_resp = await client.post(REFRESH_URL, json={"refresh_token": refresh})
    assert refresh_resp.status_code == 401


async def test_self_reset_revokes_own_pat(client, user_a_token):
    """Свои PAT'ы юзера revoke'ятся при смене пароля.

    Защита от sticky-takeover: атакующий с угнанным access-токеном
    создаёт PAT, юзер меняет пароль — PAT обязан умереть, иначе
    атакующий держит доступ дальше TTL.
    """
    pat_resp = await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "self_reset_pat", "allowed_services": ["service_x"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )
    assert pat_resp.status_code == 201
    pat_token = pat_resp.json()["token"]

    change_resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "User12345678!", "new_password": "NewSecret9012345!"},
    )
    assert change_resp.status_code == 200

    introspect = await client.post(INTROSPECT_URL, json={"token": pat_token})
    assert introspect.status_code == 200
    assert introspect.json()["active"] is False


async def test_self_reset_does_not_touch_other_users_pats(
    client, user_a_token, db, dept_a_with_service, service_x,
):
    """change_own_password юзера A не трогает PAT юзера B.

    Bulk-revoke в репозитории идёт по `user_id` — регрессия-canary против
    случайного расширения WHERE.
    """
    from tests.conftest import _make_user, _assign_role, _login

    user_b = await _make_user(
        db, "t_user_b_pat", "User12345678!", department_id=dept_a_with_service.id,
    )
    await _assign_role(db, user_b.id, service_x.service_name, "reader")
    await db.commit()
    user_b_token = await _login(client, "t_user_b_pat", "User12345678!")

    pat_b_resp = await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_b_token}"},
        json={"name": "user_b_pat", "allowed_services": ["service_x"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )
    assert pat_b_resp.status_code == 201, pat_b_resp.text
    pat_b_token = pat_b_resp.json()["token"]

    change_resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "User12345678!", "new_password": "NewSecret9012345!"},
    )
    assert change_resp.status_code == 200

    introspect = await client.post(INTROSPECT_URL, json={"token": pat_b_token})
    assert introspect.status_code == 200
    assert introspect.json()["active"] is True


async def test_self_reset_preserves_department_bots(
    client, user_a_token, dept_admin_a_token, dept_a_with_service, service_x,
):
    """Bot-токены департамента переживают смену пароля одного юзера.

    Боты — отдельная identity отдела, не привязаны к конкретному юзеру.
    """
    bot_resp = await client.post(
        "/api/auth/v1/bots",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={
            "name": "ci_bot_self_reset",
            "department_id": dept_a_with_service.id,
            "allowed_services": ["service_x"],
        },
    )
    assert bot_resp.status_code == 201, bot_resp.text
    bot_id = bot_resp.json()["bot_id"]

    bot_token_resp = await client.post(
        f"/api/auth/v1/bots/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"name": "ci", "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )
    assert bot_token_resp.status_code == 201, bot_token_resp.text
    bot_token = bot_token_resp.json()["token"]

    change_resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "User12345678!", "new_password": "NewSecret9012345!"},
    )
    assert change_resp.status_code == 200

    introspect = await client.post(INTROSPECT_URL, json={"token": bot_token})
    assert introspect.status_code == 200
    assert introspect.json()["active"] is True


# ── Admin self-reset emits CRITICAL audit ───────────────────────────────────


async def test_admin_self_reset_emits_audit_with_caller_is_admin(
    client, admin_token, monkeypatch
):
    """account_admin меняет себе пароль → audit `user.self_password_reset` с caller_is_admin=true.

    Default severity для action — CRITICAL (см. audit_events.py). Здесь
    проверяется именно payload — что флаг admin'а есть в details, чтобы SIEM
    мог фильтровать admin-self-reset'ы отдельно от обычных юзерских.
    """
    captured: list[dict] = []
    from src.services import audit_service as _audit

    real_emit = _audit.emit

    def _spy(action, *args, **kwargs):
        if action == "user.self_password_reset":
            captured.append({"action": action, "kwargs": kwargs})
        return real_emit(action, *args, **kwargs)

    monkeypatch.setattr(_audit, "emit", _spy)
    # user_service импортирует `audit_service` модулем, поэтому monkeypatch
    # самого `audit_service.emit` достаточно — атрибут резолвится в рантайме.

    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"old_password": "Admin12345678!", "new_password": "NewAdmin90123456!"},
    )
    assert resp.status_code == 200, resp.text

    assert len(captured) == 1, f"ожидался ровно один self_password_reset audit, got {captured}"
    details = captured[0]["kwargs"]["details"]
    assert details["caller_is_admin"] is True
    assert details["sessions_revoked"] is True
    assert details["tokens_revoked"] is True
    assert details["pat_revoked_count"] == 0


async def test_regular_user_self_reset_audit_has_caller_is_admin_false(
    client, user_a_token, monkeypatch
):
    """Обычный юзер self-reset → audit с caller_is_admin=false (для SIEM-фильтра)."""
    captured: list[dict] = []
    from src.services import audit_service as _audit

    real_emit = _audit.emit

    def _spy(action, *args, **kwargs):
        if action == "user.self_password_reset":
            captured.append({"action": action, "kwargs": kwargs})
        return real_emit(action, *args, **kwargs)

    monkeypatch.setattr(_audit, "emit", _spy)

    resp = await client.post(
        URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"old_password": "User12345678!", "new_password": "NewSecret9012345!"},
    )
    assert resp.status_code == 200

    assert len(captured) == 1
    assert captured[0]["kwargs"]["details"]["caller_is_admin"] is False


# ── Unauthenticated → 401 ───────────────────────────────────────────────────


async def test_unauthenticated_request_rejected(client):
    """Без Bearer-токена endpoint отбивается 401."""
    resp = await client.post(
        URL,
        json={"old_password": "User12345678!", "new_password": "NewSecret9012345!"},
    )
    assert resp.status_code == 401
