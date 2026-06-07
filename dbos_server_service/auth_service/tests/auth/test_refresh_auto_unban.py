"""refresh() симметрично login делает auto-unban для истёкшего temporary ban.

До фикса SPA с фоновой ротацией access-токена ловил 401 USER_BANNED после
истечения temporary ban'а — login разбанивал inline, refresh — нет.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from src.core.security import hash_refresh_token
from src.models import Ban, Session, User


USERS_URL = "/api/auth/v1/users"
LOGIN_URL = "/api/auth/v1/login"
REFRESH_URL = "/api/auth/v1/refresh"


async def test_refresh_with_expired_temp_ban_auto_unbans_and_rotates(
    client, admin_token, user_a, db,
):
    """Истёкший temp-ban на момент refresh — auto-unban inline, рефреш отрабатывает 200."""
    user_a_id = user_a.id
    login = await client.post(
        LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
    )
    assert login.status_code == 200, login.text
    rt = login.json()["refresh_token"]

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    ban_resp = await client.post(
        f"{USERS_URL}/{user_a_id}/ban",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ban_type": "temporary", "reason": "expiry-test", "expires_at": future},
    )
    assert ban_resp.status_code == 200, ban_resp.text

    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    await db.execute(update(Ban).where(Ban.user_id == user_a_id).values(expires_at=past))
    # `ban_user` валит все active-сессии — для симуляции «refresh-token дожил
    # до момента, когда ban уже отгорел» вручную реактивируем строку. В проде
    # тот же сценарий возникает, если admin успел разлогинить-и-перевыдать
    # refresh либо если revoke пропустили из-за race-кейса.
    rt_hash = hash_refresh_token(rt)
    await db.execute(
        update(Session)
        .where(Session.refresh_token_hash == rt_hash)
        .values(is_active=True, revoked_at=None)
    )
    await db.commit()

    resp = await client.post(REFRESH_URL, json={"refresh_token": rt})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["refresh_token"] != rt

    db.expire_all()
    refreshed = await db.scalar(select(User).where(User.id == user_a_id))
    assert refreshed is not None
    assert refreshed.status == "active"
    assert refreshed.is_active is True


async def test_refresh_with_live_ban_still_fails_with_user_banned(
    client, admin_token, user_a, db,
):
    """Живой ban (expires_at в будущем) — refresh продолжает отбивать 403 USER_BANNED."""
    login = await client.post(
        LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
    )
    assert login.status_code == 200, login.text
    rt = login.json()["refresh_token"]

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    ban_resp = await client.post(
        f"{USERS_URL}/{user_a.id}/ban",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ban_type": "temporary", "reason": "still-live", "expires_at": future},
    )
    assert ban_resp.status_code == 200, ban_resp.text

    # Симметрично первому тесту реактивируем сессию, чтобы refresh добрался
    # до ban-guard'а в `auth_service.refresh`, а не отскочил на этапе lookup.
    rt_hash = hash_refresh_token(rt)
    await db.execute(
        update(Session)
        .where(Session.refresh_token_hash == rt_hash)
        .values(is_active=True, revoked_at=None)
    )
    await db.commit()

    resp = await client.post(REFRESH_URL, json={"refresh_token": rt})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BANNED"
