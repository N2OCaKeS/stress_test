"""refresh() публикует audit на denied-ветках (banned / blocked / user_not_found).

Раньше эти ветки уходили в `Authorization`/`AuthenticationError` без emit'а —
SIEM не видел отказ при ротации, в отличие от login(), где такие случаи давно
журналируются. Закрываем расхождение.
"""

import pytest
from sqlalchemy import update

from src.models import User


URL = "/api/auth/v1/refresh"
LOGIN_URL = "/api/auth/v1/login"


@pytest.fixture()
def captured_audit(monkeypatch):
    """Перехватываем audit_service.* posts в список."""
    captured: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):
        captured.append(json)

    class _AsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json, headers):
            captured.append(json)
            class R:
                status_code = 201
            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.post", fake_sync_post)
    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type("S", (), {
            "logging_service_url": "http://test",
            "logging_service_api_key": "k",
        })(),
    )
    return captured


def _refresh_failures(captured: list[dict], reason: str) -> list[dict]:
    return [
        e for e in captured
        if e.get("action") == "user.refresh"
        and e.get("status") == "failure"
        and (e.get("details") or {}).get("reason") == reason
    ]


async def test_refresh_banned_user_emits_audit(client, db, account_admin, captured_audit):
    login = await client.post(
        LOGIN_URL, json={"username": "t_admin", "password": "Admin1234!"}
    )
    rt = login.json()["refresh_token"]

    # Сразу баним юзера.
    await db.execute(
        update(User).where(User.id == account_admin.id).values(status="banned")
    )
    await db.commit()

    resp = await client.post(URL, json={"refresh_token": rt})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BANNED"

    events = _refresh_failures(captured_audit, "banned")
    assert events, f"user.refresh banned audit not emitted: {captured_audit}"


async def test_refresh_blocked_user_emits_audit(client, db, account_admin, captured_audit):
    login = await client.post(
        LOGIN_URL, json={"username": "t_admin", "password": "Admin1234!"}
    )
    rt = login.json()["refresh_token"]

    await db.execute(
        update(User).where(User.id == account_admin.id).values(status="blocked")
    )
    await db.commit()

    resp = await client.post(URL, json={"refresh_token": rt})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BLOCKED"

    events = _refresh_failures(captured_audit, "blocked")
    assert events, f"user.refresh blocked audit not emitted: {captured_audit}"


async def test_refresh_expired_session_emits_audit(
    client, db, account_admin, captured_audit,
):
    """Просроченная refresh-сессия → 401 REFRESH_TOKEN_EXPIRED + denied audit."""
    login = await client.post(
        LOGIN_URL, json={"username": "t_admin", "password": "Admin1234!"}
    )
    rt = login.json()["refresh_token"]

    from datetime import datetime, timedelta, timezone

    from src.core.security import hash_refresh_token
    from src.models import Session

    past = datetime.now(timezone.utc) - timedelta(days=1)
    th = hash_refresh_token(rt)
    sess = (await db.execute(
        Session.__table__.select().where(Session.refresh_token_hash == th)
    )).first()
    assert sess is not None, "session not found by refresh-token hash"

    await db.execute(
        Session.__table__.update()
        .where(Session.refresh_token_hash == th)
        .values(expires_at=past)
    )
    await db.commit()

    resp = await client.post(URL, json={"refresh_token": rt})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "REFRESH_TOKEN_EXPIRED"

    events = _refresh_failures(captured_audit, "expired")
    assert events, f"user.refresh expired audit not emitted: {captured_audit}"


async def test_refresh_missing_user_emits_audit(
    client, db, account_admin, monkeypatch, captured_audit,
):
    """Сессия живая, лук user возвращает None → emit failure reason=user_not_found.

    Прямая mutation users в БД невозможна (FK ON DELETE CASCADE убивает сессию
    вместе с юзером), поэтому мокаем `UserRepository.get_by_id` — это ровно та
    точка, на которой ветка `user is None` срабатывает в проде при гонке
    delete-user → refresh.
    """
    login = await client.post(
        LOGIN_URL, json={"username": "t_admin", "password": "Admin1234!"}
    )
    rt = login.json()["refresh_token"]

    from src.repositories.users import UserRepository

    async def _none(self, uid):
        return None

    monkeypatch.setattr(UserRepository, "get_by_id", _none)

    resp = await client.post(URL, json={"refresh_token": rt})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_NOT_FOUND"

    events = _refresh_failures(captured_audit, "user_not_found")
    assert events, f"user.refresh user_not_found audit not emitted: {captured_audit}"
