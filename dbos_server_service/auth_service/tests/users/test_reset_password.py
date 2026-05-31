"""Тесты: POST /api/auth/v1/users/{id}/reset-password — сброс пароля пользователя."""

URL_TPL = "/api/auth/v1/users/{user_id}/reset-password"
LOGIN_URL = "/api/auth/v1/login"


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
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})
    assert resp.status_code == 401


async def test_reset_revokes_active_sessions(client, admin_token, user_a):
    login_data = (await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})).json()
    await client.post(URL_TPL.format(user_id=user_a.id),
                      headers={"Authorization": f"Bearer {admin_token}"},
                      json={"new_password": "NewPass1234!"})
    resp = await client.post("/api/auth/v1/refresh", json={"refresh_token": login_data["refresh_token"]})
    assert resp.status_code == 401


async def test_regular_user_cannot_reset_password(client, user_a_token, user_b):
    resp = await client.post(URL_TPL.format(user_id=user_b.id),
                              headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"new_password": "Hacked1234!"})
    assert resp.status_code == 403


async def test_reset_nonexistent_user_returns_404(client, admin_token):
    resp = await client.post(URL_TPL.format(user_id="usr_nonexistent"),
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"new_password": "Pass1234!"})
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
    """dept_admin_a → reset password юзера dep_b → 403 DEPARTMENT_ISOLATION."""
    resp = await client.post(
        URL_TPL.format(user_id=user_b.id),
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"new_password": "Hacked1234!"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"


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


# ── actor_role / actor_department_id из IdentityContext'а ──────────────────
#
# Endpoint прокидывает оба поля в service-функцию, и она не идёт в БД за
# actor.row, когда роль и dept_id уже известны. Проверяем service-функцию
# напрямую (минуя endpoint, чтобы не считать SELECT'ы из identity-resolver'а).


async def test_reset_password_account_admin_skips_actor_select(db, user_a):
    """account_admin: guard не активен → лишний SELECT actor'а не происходит.

    Сравниваем счётчик `UserRepository.get_by_id` вызовов внутри сервиса —
    должен быть ровно один (для target'а).
    """
    from src.services import user_service
    from src.repositories.users import UserRepository
    from src.core.constants import PlatformRole

    real_get_by_id = UserRepository.get_by_id
    select_ids: list[str] = []

    async def _spy(self, uid):
        select_ids.append(uid)
        return await real_get_by_id(self, uid)

    UserRepository.get_by_id = _spy  # type: ignore[method-assign]
    try:
        await user_service.reset_password(
            db=db,
            actor_id="usr_fake_admin",
            user_id=user_a.id,
            new_password="NewPass1234!",
            actor_role=PlatformRole.ACCOUNT_ADMIN,
            actor_department_id=None,
        )
    finally:
        UserRepository.get_by_id = real_get_by_id  # type: ignore[method-assign]

    # SELECT'ов ровно один — target user.
    assert select_ids == [user_a.id], select_ids


async def test_reset_password_dept_admin_uses_identity_department(db, user_a):
    """dept_admin: cross-dept guard читает dept_id из IdentityContext без SELECT'а actor'а."""
    from src.services import user_service
    from src.repositories.users import UserRepository
    from src.core.constants import PlatformRole

    real_get_by_id = UserRepository.get_by_id
    select_ids: list[str] = []

    async def _spy(self, uid):
        select_ids.append(uid)
        return await real_get_by_id(self, uid)

    UserRepository.get_by_id = _spy  # type: ignore[method-assign]
    try:
        await user_service.reset_password(
            db=db,
            actor_id="usr_fake_dept_admin",
            user_id=user_a.id,
            new_password="NewPass1234!",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            actor_department_id=user_a.department_id,
        )
    finally:
        UserRepository.get_by_id = real_get_by_id  # type: ignore[method-assign]

    # Опять — только target SELECT, actor берём из identity.
    assert select_ids == [user_a.id], select_ids
