"""Тесты: лимит одновременных сессий юзера (MAX_CONCURRENT_SESSIONS).

На login сверх лимита самые старые сессии вытесняются (sliding window),
вход не блокируется. Лимит касается только интерактивных user-сессий —
PAT и bot-токены сессиями не являются.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.core.config import get_settings
from src.core.security import hash_password
from src.models import Session, User
from src.utils.ids import _new_id
from tests._helpers.http import login as _login

REFRESH_URL = "/api/auth/v1/refresh"


async def _active_sessions(db, user_id: str) -> list[Session]:
    result = await db.scalars(
        select(Session)
        .where(Session.user_id == user_id, Session.is_active.is_(True))
        .order_by(Session.created_at.asc())
    )
    return list(result)


async def _make_active_user(db, username: str) -> str:
    """Создаёт активного юзера, возвращает его id (строкой — без ORM-lazy)."""
    user = User(
        id=_new_id("usr_"),
        username=username,
        password_hash=hash_password("Pass12345678!"),
        status="active",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user.id


async def test_third_login_evicts_oldest(client, db):
    """3-й логин → активных ровно 2, отозвана самая старая."""
    user_id = await _make_active_user(db, "sl_user")

    s1 = await _login(client, "sl_user", "Pass12345678!")
    s2 = await _login(client, "sl_user", "Pass12345678!")
    s3 = await _login(client, "sl_user", "Pass12345678!")

    active = await _active_sessions(db, user_id)
    assert len(active) == 2

    # Самая старая (от первого логина) больше не активна → refresh не проходит.
    resp_old = await client.post(REFRESH_URL, json={"refresh_token": s1["refresh_token"]})
    assert resp_old.status_code == 401

    # Две последние сессии живы — refresh работает.
    resp_s2 = await client.post(REFRESH_URL, json={"refresh_token": s2["refresh_token"]})
    assert resp_s2.status_code == 200
    resp_s3 = await client.post(REFRESH_URL, json={"refresh_token": s3["refresh_token"]})
    assert resp_s3.status_code == 200


async def test_limit_disabled_keeps_all_sessions(client, db, monkeypatch):
    """MAX_CONCURRENT_SESSIONS=0 → лимита нет, все сессии остаются."""
    monkeypatch.setattr(get_settings(), "max_concurrent_sessions", 0)
    user_id = await _make_active_user(db, "sl_nolimit")

    for _ in range(4):
        await _login(client, "sl_nolimit", "Pass12345678!")

    active = await _active_sessions(db, user_id)
    assert len(active) == 4


async def test_eviction_emits_audit(client, db, capture_audit_payloads):
    """Вытеснение эмитит событие user.session_evicted_over_limit."""
    user_id = await _make_active_user(db, "sl_audit")

    await _login(client, "sl_audit", "Pass12345678!")
    await _login(client, "sl_audit", "Pass12345678!")
    await _login(client, "sl_audit", "Pass12345678!")

    evictions = [
        p for p in capture_audit_payloads
        if p["action"] == "user.session_evicted_over_limit"
    ]
    assert len(evictions) == 1
    ev = evictions[0]
    assert ev["actor_id"] == user_id
    assert ev["target_type"] == "session"
    assert ev["details"]["reason"] == "max_concurrent_sessions"
    assert ev["details"]["limit"] == 2


async def test_limit_is_per_user(client, db):
    """Лимит одного юзера не задевает сессии другого."""
    user_one_id = await _make_active_user(db, "sl_one")
    user_two_id = await _make_active_user(db, "sl_two")

    # Забиваем лимит user_one (3 логина → 2 активных).
    for _ in range(3):
        await _login(client, "sl_one", "Pass12345678!")
    # У user_two — две независимые сессии, обе должны жить.
    await _login(client, "sl_two", "Pass12345678!")
    await _login(client, "sl_two", "Pass12345678!")

    assert len(await _active_sessions(db, user_one_id)) == 2
    assert len(await _active_sessions(db, user_two_id)) == 2


async def test_pat_not_counted_as_session(client, db, user_a):
    """PAT — не сессия: его выпуск не плодит и не вытесняет refresh-сессии."""
    logged = await _login(client, "t_user_a", "User12345678!")
    token = logged["access_token"]

    # Один активный login → одна сессия.
    before = await _active_sessions(db, user_a.id)
    assert len(before) == 1

    exp = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    resp = await client.post(
        "/api/auth/v1/tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "ci-token", "allowed_services": ["service_x"], "expires_at": exp},
    )
    assert resp.status_code == 201, resp.text

    # PAT не создаёт Session-строк — счётчик сессий не изменился.
    after = await _active_sessions(db, user_a.id)
    assert len(after) == 1
