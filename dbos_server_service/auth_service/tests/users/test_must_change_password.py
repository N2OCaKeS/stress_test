"""Тесты: принудительная смена пароля (must_change_password).

Покрывает:
  * Юзер с `must_change_password=True` получает 403 PASSWORD_CHANGE_REQUIRED
    на любом endpoint'е, кроме whitelist'а (`/me/password`, `/logout`,
    `/health`, `/ready`).
  * `/health` пропускается без bearer'а.
  * Успешный self-change через `/me/password` сбрасывает флаг → следующий
    запрос идёт нормально.
  * `POST /users` (создание юзера админом) выставляет флаг новому юзеру.
  * `POST /users/{id}/reset-password` (admin сброс) выставляет флаг target'у.
  * Identity-cache не удерживает stale True после сброса.
"""

import pytest

from src.dependencies.auth import (
    _IDENTITY_CACHE_TTL_SECONDS as _ORIG_TTL,
    _identity_cache_clear,
)

LOGIN_URL = "/api/auth/v1/login"
ME_URL = "/api/auth/v1/me"
ME_PASSWORD_URL = "/api/auth/v1/users/me/password"
HEALTH_URL = "/api/auth/v1/health"
LIST_USERS_URL = "/api/auth/v1/users"
TOKENS_URL = "/api/auth/v1/tokens"


async def _login(client, username, password):
    resp = await client.post(LOGIN_URL, json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ── Юзер с must_change=True блокируется на всех не-whitelist endpoint'ах ────


async def test_must_change_blocks_me(client, db, user_a):
    """GET /users/me → 403 PASSWORD_CHANGE_REQUIRED."""
    user_a.must_change_password = True
    await db.commit()
    token = await _login(client, "t_user_a", "User1234!")

    resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["error_code"] == "PASSWORD_CHANGE_REQUIRED"
    assert body["details"]["allowed_endpoint"] == ME_PASSWORD_URL


async def test_must_change_blocks_list_users(client, db, account_admin):
    """GET /users → 403."""
    account_admin.must_change_password = True
    await db.commit()
    token = await _login(client, "t_admin", "Admin1234!")

    resp = await client.get(LIST_USERS_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PASSWORD_CHANGE_REQUIRED"


async def test_must_change_blocks_post_tokens(client, db, user_a):
    """POST /tokens → 403."""
    user_a.must_change_password = True
    await db.commit()
    token = await _login(client, "t_user_a", "User1234!")

    resp = await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "pat_blocked", "allowed_services": ["service_x"]},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PASSWORD_CHANGE_REQUIRED"


# ── Whitelist: health пропускается ─────────────────────────────────────────


async def test_health_ok_anonymous(client):
    """GET /health всегда 200 (без bearer)."""
    resp = await client.get(HEALTH_URL)
    assert resp.status_code == 200


async def test_health_ok_with_must_change_user(client, db, user_a):
    """GET /health пропускается даже если у юзера флаг (хотя bearer тут вообще не валидируется)."""
    user_a.must_change_password = True
    await db.commit()
    token = await _login(client, "t_user_a", "User1234!")

    resp = await client.get(HEALTH_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# ── /me/password пропускается → флаг сбрасывается → следующий запрос ОК ────


async def test_change_password_clears_flag(client, db, user_a):
    """POST /me/password успешный → флаг = False → GET /me → 200."""
    user_a.must_change_password = True
    await db.commit()
    token = await _login(client, "t_user_a", "User1234!")

    # Сначала убедимся, что флаг блокирует.
    blocked = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 403

    # Смена пароля проходит (это whitelist).
    change = await client.post(
        ME_PASSWORD_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "User1234!", "new_password": "NewSecret9!"},
    )
    assert change.status_code == 200, change.text

    # `change_own_password` revoke'ит все сессии — нужен новый login.
    new_token = await _login(client, "t_user_a", "NewSecret9!")
    ok = await client.get(ME_URL, headers={"Authorization": f"Bearer {new_token}"})
    assert ok.status_code == 200


# ── admin сменил пароль чужому юзеру → флаг ставится у того ────────────────


async def test_admin_reset_sets_must_change(client, db, admin_token, user_a):
    """POST /users/{id}/reset-password → у target'а флаг True."""
    # Чистим начальное значение, чтобы тест не зависел от других source'ов.
    user_a.must_change_password = False
    await db.commit()

    resp = await client.post(
        f"/api/auth/v1/users/{user_a.id}/reset-password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_password": "AdminSet9!"},
    )
    assert resp.status_code == 200

    # Перечитываем юзера из БД.
    await db.refresh(user_a)
    assert user_a.must_change_password is True

    # И поведенчески: login + любой gated endpoint → 403.
    token = await _login(client, "t_user_a", "AdminSet9!")
    blocked = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 403
    assert blocked.json()["error_code"] == "PASSWORD_CHANGE_REQUIRED"


# ── dep_admin создал юзера → флаг ставится у нового ────────────────────────


async def test_create_user_sets_must_change(client, db, dept_admin_a_token, dept_a_with_service):
    """POST /users → новый юзер с must_change_password=True."""
    resp = await client.post(
        LIST_USERS_URL,
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={
            "username": "must_change_new",
            "password": "Temp1234!",
            "department_id": dept_a_with_service.id,
        },
    )
    assert resp.status_code == 201, resp.text

    # Логинимся как новый юзер — на любой gated endpoint должно прилетать 403.
    token = await _login(client, "must_change_new", "Temp1234!")
    blocked = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 403
    assert blocked.json()["error_code"] == "PASSWORD_CHANGE_REQUIRED"

    # /me/password проходит и сбрасывает флаг.
    change = await client.post(
        ME_PASSWORD_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "Temp1234!", "new_password": "AfterTemp9!"},
    )
    assert change.status_code == 200


# ── identity-cache не удерживает stale True после сброса ───────────────────


@pytest.fixture()
def _identity_cache_enabled(monkeypatch):
    """Включить TTL > 0 для покрытия инвалидации.

    Все остальные тесты идут с TTL=0 (см. conftest), но здесь нам нужно
    проверить именно invalidate-хук на смене пароля.
    """
    import src.dependencies.auth as deps

    monkeypatch.setattr(deps, "_IDENTITY_CACHE_TTL_SECONDS", 60.0)
    monkeypatch.setattr(deps, "_IDENTITY_CACHE_MAXSIZE", 100)
    _identity_cache_clear()
    yield
    _identity_cache_clear()


async def test_change_own_password_invalidates_identity_cache(
    client, db, user_a, _identity_cache_enabled
):
    """change_own_password дёргает invalidate_identity_cache_for_user → следующий
    запрос видит must_change=False, не stale True из кэша."""
    user_a.must_change_password = True
    await db.commit()
    token = await _login(client, "t_user_a", "User1234!")

    # Прогреваем кэш (одним 403 на /me) — теперь там IdentityContext с must_change=True.
    first = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert first.status_code == 403

    # Смена пароля сбрасывает флаг + invalidate'ит кэш.
    change = await client.post(
        ME_PASSWORD_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "User1234!", "new_password": "NewSecret9!"},
    )
    assert change.status_code == 200

    # change_own_password revoke'ит сессии — новый login для проверки.
    new_token = await _login(client, "t_user_a", "NewSecret9!")
    ok = await client.get(ME_URL, headers={"Authorization": f"Bearer {new_token}"})
    assert ok.status_code == 200


# Sanity: _ORIG_TTL импортирован, чтобы lint не ругался на неиспользованный
# импорт; значение нам нужно только косвенно для документации поведения.
_ = _ORIG_TTL
