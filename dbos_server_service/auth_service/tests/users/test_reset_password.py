"""Тесты: POST /api/auth/v1/users/{id}/reset-password — сброс пароля пользователя."""

from datetime import timedelta

from src.utils.time import utcnow

URL_TPL = "/api/auth/v1/users/{user_id}/reset-password"
LOGIN_URL = "/api/auth/v1/login"
TOKENS_URL = "/api/auth/v1/tokens"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def test_admin_resets_password(client, admin_token, user_a):
    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_password": "NewPass1234!"},
    )
    assert resp.status_code == 200


async def test_new_password_works_for_login(client, admin_token, user_a):
    await client.post(URL_TPL.format(user_id=user_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"new_password": "NewPass1234!"})
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "NewPass1234!"})
    assert resp.status_code == 200


async def test_old_password_no_longer_works(client, admin_token, user_a):
    await client.post(URL_TPL.format(user_id=user_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"new_password": "NewPass1234!"})
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"})
    assert resp.status_code == 401


async def test_reset_revokes_active_sessions(client, admin_token, user_a):
    login_data = (await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"})).json()
    await client.post(URL_TPL.format(user_id=user_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"new_password": "NewPass1234!"})
    resp = await client.post("/api/auth/v1/refresh", json={"refresh_token": login_data["refresh_token"]})
    assert resp.status_code == 401


async def test_admin_reset_revokes_target_pats(client, admin_token, user_a_token, user_a):
    """Admin-reset чужого пароля отзывает PAT'ы целевого юзера.

    Закрывает sticky-takeover: если кто-то держал чужой PAT, выписанный до
    сброса, после admin-reset'а он не должен переживать смену пароля.
    """
    pat_resp = await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={
            "name": "before_admin_reset",
            "allowed_services": ["service_x"],
            "expires_at": (utcnow() + timedelta(days=30)).isoformat(),
        },
    )
    assert pat_resp.status_code == 201, pat_resp.text
    pat_token = pat_resp.json()["token"]

    reset = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_password": "NewPass1234!"},
    )
    assert reset.status_code == 200, reset.text

    introspect = await client.post(INTROSPECT_URL, json={"token": pat_token})
    assert introspect.status_code == 200
    assert introspect.json()["active"] is False


async def test_regular_user_cannot_reset_password(client, user_a_token, user_b):
    resp = await client.post(URL_TPL.format(user_id=user_b.id),
                              headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"new_password": "Hacked12345678!"})
    assert resp.status_code == 403


async def test_reset_nonexistent_user_returns_404(client, admin_token):
    resp = await client.post(URL_TPL.format(user_id="usr_nonexistent"),
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"new_password": "Pass12345678!"})
    assert resp.status_code == 404


# ── Cross-department escalation ───────────────────────────────────────────
#
# Раньше endpoint `POST /users/{id}/reset-password` имел `AnyAdmin` guard,
# но `services/user_service.reset_password` не сравнивал `actor.department_id`
# с `target.department_id`. department_admin отдела A мог сбрасывать пароли
# юзерам отдела B → privilege escalation.
# Фикс: явная department-проверка для `DEPARTMENT_ADMIN`-actor'а.

async def test_dept_admin_cannot_reset_password_cross_department(
    client, dept_admin_a_token, user_b,
):
    """dept_admin_a → reset password юзера dep_b → 403 USER_RESET_PASSWORD_FORBIDDEN."""
    resp = await client.post(
        URL_TPL.format(user_id=user_b.id),
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"new_password": "Hacked12345678!"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_RESET_PASSWORD_FORBIDDEN"


async def test_dept_admin_can_reset_password_within_department(
    client, dept_admin_a_token, user_a,
):
    """dept_admin_a → reset password юзера dep_a → 200 (own department)."""
    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"new_password": "NewPass1234!"},
    )
    assert resp.status_code == 200


async def test_account_admin_can_reset_any_user(client, admin_token, user_b):
    """account_admin сбрасывает пароль кросс-dept-юзеру → 200.

    Регрессия-страховка: фикс DEPARTMENT_ISOLATION не должен отбивать
    account_admin'а, у которого `department_id is None` и `platform_role`
    отличается от DEPARTMENT_ADMIN.
    """
    resp = await client.post(
        URL_TPL.format(user_id=user_b.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_password": "NewPass1234!"},
    )
    assert resp.status_code == 200


# ── reset_password должен сбрасывать identity-cache юзера ────────────────────


async def test_reset_password_invalidates_identity_cache(
    client, admin_token, user_a, monkeypatch,
):
    """Identity-cache юзера сбрасывается сразу после reset, не по TTL."""
    invalidated: list[str] = []

    def _spy(uid):
        invalidated.append(uid)

    monkeypatch.setattr(
        "src.services.user_service._invalidate_identity_cache", _spy
    )

    resp = await client.post(
        URL_TPL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_password": "NewPass1234!"},
    )
    assert resp.status_code == 200
    assert user_a.id in invalidated, (
        f"identity-cache юзера должен быть сброшен, got {invalidated}"
    )


# ── ACTOR_VANISHED fail-closed ─────────────────────────────────────────────
#
# Раньше DA-ветка `reset_password` имела legacy `if actor and ...`-проверку:
# когда actor успевал быть удалён между issue JWT и check'ом, проверка
# коротила в False, и reset проходил кросс-департаментно (плюс revoke сессий
# жертве). Теперь зовём `assert_dept_admin_target_dept` — он бросает
# ACTOR_VANISHED, и side-effects (revoke сессий/PAT) не наступают.


async def test_reset_password_dept_admin_broken_identity_blocked(db, user_a):
    """DA с actor_id, которого нет в БД → 403 ACTOR_VANISHED, side-effects не выполняются."""
    from src.services import user_service
    from src.repositories.sessions import SessionRepository
    from src.repositories.tokens import TokenRepository
    from src.core.constants import PlatformRole
    from src.core.exceptions import AuthorizationError
    import pytest

    revoke_sessions_calls: list[str] = []
    revoke_tokens_calls: list[str] = []

    real_revoke_sessions = SessionRepository.revoke_all_for_user
    real_revoke_tokens = TokenRepository.revoke_all_for_user

    async def _spy_sessions(self, uid):
        revoke_sessions_calls.append(uid)
        return await real_revoke_sessions(self, uid)

    async def _spy_tokens(self, uid):
        revoke_tokens_calls.append(uid)
        return await real_revoke_tokens(self, uid)

    SessionRepository.revoke_all_for_user = _spy_sessions  # type: ignore[method-assign]
    TokenRepository.revoke_all_for_user = _spy_tokens  # type: ignore[method-assign]
    try:
        with pytest.raises(AuthorizationError) as excinfo:
            await user_service.reset_password(
                db=db,
                actor_id="usr_nonexistent_da",
                user_id=user_a.id,
                new_password="NewPass1234!",
                actor_role=PlatformRole.DEPARTMENT_ADMIN,
            )
    finally:
        SessionRepository.revoke_all_for_user = real_revoke_sessions  # type: ignore[method-assign]
        TokenRepository.revoke_all_for_user = real_revoke_tokens  # type: ignore[method-assign]

    assert excinfo.value.error_code == "ACTOR_VANISHED"
    assert revoke_sessions_calls == []
    assert revoke_tokens_calls == []
