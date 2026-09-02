"""Отзыв сессии бьёт и по собственной auth-зависимости auth_service (`/me`).

`get_current_identity` (через `_identity_from_user_jwt`) при наличии `sid`
сверяет refresh-сессию: revoked/неактивная → 401, как для inactive-юзера.
Так /me и прочие user-эндпоинты auth_service тоже мгновенно отбивают токен
отозванной сессии, не дожидаясь истечения TTL.

Что проверяем:
* активная сессия → /me 200;
* logout → /me 401;
* прямой revoke сессии в БД → /me 401;
* токен без `sid` → /me работает (проверка сессии не применяется).
"""

from datetime import timedelta

from src.core.security import create_access_token, decode_access_token
from src.repositories.sessions import SessionRepository
from tests._helpers.http import login as _login_full

ME_URL = "/api/auth/v1/me"
LOGOUT_URL = "/api/auth/v1/logout"


async def _me(client, token):
    return await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})


async def test_me_ok_with_active_session(client, user_a):
    tokens = await _login_full(client, "t_user_a", "User12345678!")
    resp = await _me(client, tokens["access_token"])
    assert resp.status_code == 200, resp.text


async def test_me_401_after_logout(client, user_a):
    tokens = await _login_full(client, "t_user_a", "User12345678!")
    access = tokens["access_token"]
    assert (await _me(client, access)).status_code == 200

    logout = await client.post(LOGOUT_URL, json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 200, logout.text

    resp = await _me(client, access)
    assert resp.status_code == 401, resp.text


async def test_me_401_after_db_revoke(client, user_a, db):
    tokens = await _login_full(client, "t_user_a", "User12345678!")
    sid = decode_access_token(tokens["access_token"])["sid"]

    sess = await SessionRepository(db).get_by_id(sid)
    assert sess is not None
    await SessionRepository(db).revoke(sess)
    await db.commit()

    assert (await _me(client, tokens["access_token"])).status_code == 401


async def test_me_token_without_sid_unaffected(client, user_a):
    """JWT без `sid` не проверяется по сессии — /me работает как раньше."""
    token = create_access_token(
        {"sub": user_a.id, "actor_type": "user"},
        expires_delta=timedelta(minutes=10),
    )
    resp = await _me(client, token)
    assert resp.status_code == 200, resp.text
