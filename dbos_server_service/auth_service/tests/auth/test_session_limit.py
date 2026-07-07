"""Тесты: лимит одновременных сессий юзера (MAX_CONCURRENT_SESSIONS).

На login сверх лимита самые старые сессии вытесняются (sliding window),
вход не блокируется. Лимит касается только интерактивных user-сессий —
PAT и bot-токены сессиями не являются.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.config import get_settings
from src.core.security import generate_refresh_token, hash_password, hash_refresh_token
from src.models import Session, User
from src.repositories.sessions import _LOGIN_LOCK_NAMESPACE, SessionRepository
from src.utils.ids import _new_id, session_id
from src.utils.time import expires_at, utcnow
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


async def _backdate_session(db, refresh_token: str, created_at) -> None:
    """Проставить `created_at` сессии по её refresh-токену.

    В тест-транзакции `now()` заморожен, поэтому у всех логинов одинаковый
    `created_at`, и порядок вытеснения (created_at, id) вырождается в случайный
    id-tiebreak. Явно разносим время, чтобы «самая старая» была детерминирована.
    """
    await db.execute(
        update(Session)
        .where(Session.refresh_token_hash == hash_refresh_token(refresh_token))
        .values(created_at=created_at)
    )


async def test_third_login_evicts_oldest(client, db):
    """3-й логин → активных ровно 2, отозвана самая старая."""
    user_id = await _make_active_user(db, "sl_user")

    s1 = await _login(client, "sl_user", "Pass12345678!")
    await _backdate_session(db, s1["refresh_token"], utcnow() - timedelta(hours=2))
    s2 = await _login(client, "sl_user", "Pass12345678!")
    await _backdate_session(db, s2["refresh_token"], utcnow() - timedelta(hours=1))
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


async def test_enforce_holds_advisory_lock_for_user(client, db):
    """enforce_concurrent_limit держит transaction-level advisory-lock по
    user_id: пока транзакция login'а открыта, второй коннект НЕ может взять
    тот же лок. Это и есть сериализация, не дающая двум параллельным login'ам
    пробить лимит невидимой (некоммитнутой) чужой сессией.
    """
    user_id = await _make_active_user(db, "sl_lock")
    repo = SessionRepository(db)
    sess = await repo.create(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token("lock_probe_token"),
        expires_at=expires_at(days=14),
    )
    # enforce берёт advisory-lock на текущем (никогда не коммитящемся в тесте)
    # соединении фикстуры — лок держится до конца транзакции.
    await repo.enforce_concurrent_limit(user_id=user_id, keep_session_id=sess.id, limit=2)

    # Со второго независимого соединения тот же лок должен быть недоступен.
    probe_engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with probe_engine.connect() as conn:
            await conn.begin()
            got = await conn.scalar(
                text("SELECT pg_try_advisory_xact_lock(:ns, hashtext(:uid))"),
                {"ns": _LOGIN_LOCK_NAMESPACE, "uid": user_id},
            )
            await conn.rollback()
    finally:
        await probe_engine.dispose()

    assert got is False, (
        "enforce_concurrent_limit должен держать advisory-lock по user_id "
        "(без него два параллельных login'а пробивают лимит)"
    )


async def test_concurrent_logins_do_not_exceed_limit():
    """Два одновременных login'а поверх уже заполненного лимита не пробивают
    его. Использует ДВА независимых коммитящихся соединения — shared savepoint-
    фикстура сериализует всё на одном коннекте и гонку не воспроизводит.
    Advisory-lock в enforce_concurrent_limit удерживает инвариант = limit.
    """
    from src.services import auth_service

    limit = get_settings().max_concurrent_sessions
    username = f"sl_conc_{_new_id('')}"
    password = "Pass12345678!"
    engine = create_async_engine(os.environ["DATABASE_URL"])
    user_id = None
    try:
        # Заводим юзера и «забиваем» лимит активными сессиями (committed).
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            user = User(
                id=_new_id("usr_"),
                username=username,
                password_hash=hash_password(password),
                status="active",
                is_active=True,
            )
            setup.add(user)
            await setup.flush()
            user_id = user.id
            for _ in range(limit):
                _, h = generate_refresh_token()
                setup.add(Session(
                    id=session_id(),
                    user_id=user_id,
                    refresh_token_hash=h,
                    expires_at=expires_at(days=14),
                ))
            await setup.commit()

        async def _do_login():
            async with AsyncSession(engine, expire_on_commit=False) as s:
                return await auth_service.login(s, username, password)

        results = await asyncio.gather(_do_login(), _do_login(), return_exceptions=True)
        for r in results:
            assert not isinstance(r, BaseException), f"login упал: {r!r}"

        async with AsyncSession(engine, expire_on_commit=False) as check:
            active = (await check.scalars(
                select(Session).where(
                    Session.user_id == user_id, Session.is_active.is_(True)
                )
            )).all()
        assert len(active) == limit, (
            f"лимит пробит конкурентными login'ами: {len(active)} активных "
            f"при лимите {limit}"
        )
    finally:
        if user_id is not None:
            async with AsyncSession(engine, expire_on_commit=False) as cleanup:
                await cleanup.execute(delete(Session).where(Session.user_id == user_id))
                await cleanup.execute(delete(User).where(User.id == user_id))
                await cleanup.commit()
        await engine.dispose()


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
