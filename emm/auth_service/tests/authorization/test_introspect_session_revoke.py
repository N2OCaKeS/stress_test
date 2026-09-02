"""Немедленный отзыв access-JWT при revoke его refresh-сессии.

Access-JWT несёт `sid` (id refresh-сессии). Раньше introspect не сверял
сессию — отозванный токен жил до конца TTL. Теперь при наличии `sid`
introspect проверяет, что сессия активна; revoked/неактивная → active=False.

Что проверяем:
* активная сессия → токен валиден (active=True);
* logout (revoke refresh) → тот же access-JWT → active=False;
* прямой revoke сессии в БД (is_active=False) → active=False;
* токен БЕЗ `sid` (legacy / не-сессионный) → проверка сессии не применяется.
"""

from datetime import timedelta

from src.core.security import create_access_token, decode_access_token
from src.repositories.sessions import SessionRepository
from tests._helpers.http import login as _login_full

INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
LOGOUT_URL = "/api/auth/v1/logout"


async def _introspect(client, token):
    return await client.post(INTROSPECT_URL, json={"token": token})


async def test_active_session_token_is_active(client, user_a):
    """Свежий login → сессия активна → introspect active=True."""
    tokens = await _login_full(client, "t_user_a", "User12345678!")
    resp = await _introspect(client, tokens["access_token"])
    assert resp.status_code == 200
    assert resp.json()["active"] is True


async def test_logout_revokes_access_token_immediately(client, user_a):
    """После logout (revoke refresh-сессии) access-JWT мёртв сразу, не ждёт TTL."""
    tokens = await _login_full(client, "t_user_a", "User12345678!")
    access = tokens["access_token"]

    # Токен исправен до logout.
    assert (await _introspect(client, access)).json()["active"] is True

    logout = await client.post(LOGOUT_URL, json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 200, logout.text

    assert (await _introspect(client, access)).json()["active"] is False


async def test_db_revoked_session_token_inactive(client, user_a, db):
    """Прямой revoke сессии в БД (is_active=False/revoked_at) → introspect active=False."""
    tokens = await _login_full(client, "t_user_a", "User12345678!")
    sid = decode_access_token(tokens["access_token"])["sid"]

    sess = await SessionRepository(db).get_by_id(sid)
    assert sess is not None
    await SessionRepository(db).revoke(sess)
    await db.commit()

    assert (await _introspect(client, tokens["access_token"])).json()["active"] is False


async def test_token_without_sid_not_checked_against_session(client, user_a):
    """Legacy / не-сессионный JWT без `sid` проходит introspect как раньше —
    проверка сессии применяется ТОЛЬКО при наличии `sid`."""
    token = create_access_token(
        {"sub": user_a.id, "actor_type": "user"},
        expires_delta=timedelta(minutes=10),
    )
    resp = await _introspect(client, token)
    assert resp.status_code == 200
    assert resp.json()["active"] is True
