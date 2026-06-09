"""Тесты: DELETE /api/auth/v1/users/{id} — hard-delete юзера.

Покрывает:
- happy path: 200 + row физически удалена, sessions revoked, secret_service notify;
- last-admin protection (нельзя снести последнего account_admin'а);
- guard на account_admin (department_admin не пускаем);
- 404 на несуществующего;
- best-effort нотифай secret_service не валит ответ при exception'е клиента.
"""

import pytest
from sqlalchemy import select

from src.models import Session, User
from src.services import secret_service_client

DELETE_URL = "/api/auth/v1/users/{user_id}"
LOGIN_URL = "/api/auth/v1/login"


@pytest.fixture
def notify_calls(monkeypatch):
    """Перехват `secret_service_client.notify_user_deleted` — копим аргументы
    вместо реального HTTP. По умолчанию вызов успешен."""
    calls = []

    async def _capture(user_id, actor_id, actor_username):
        calls.append({
            "user_id": user_id,
            "actor_id": actor_id,
            "actor_username": actor_username,
        })

    monkeypatch.setattr(
        secret_service_client, "notify_user_deleted", _capture
    )
    return calls


async def _hard_delete(client, token, user_id, reason="cleanup"):
    return await client.request(
        "DELETE",
        DELETE_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": reason},
    )


async def test_admin_hard_deletes_user_happy_path(
    client, admin_token, user_a, db, notify_calls,
):
    resp = await _hard_delete(client, admin_token, user_a.id, reason="left_company")
    assert resp.status_code == 200

    # Row физически снесена.
    remaining = await db.scalar(select(User).where(User.id == user_a.id))
    assert remaining is None

    # secret_service notify был вызван ровно один раз с правильными
    # аргументами (`actor_username` — username админ-actor'а из identity).
    assert len(notify_calls) == 1
    assert notify_calls[0]["user_id"] == user_a.id
    assert notify_calls[0]["actor_username"] == "t_admin"


async def test_hard_delete_revokes_sessions(
    client, admin_token, user_a, db, notify_calls,
):
    """После hard-delete у юзера не остаётся активных сессий."""
    # Создадим сессию через login.
    login = await client.post(
        LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"},
    )
    assert login.status_code == 200

    resp = await _hard_delete(client, admin_token, user_a.id)
    assert resp.status_code == 200

    # ORM-cascade физически снёс sessions при `db.delete(user)`. Здесь
    # проверяем именно отсутствие row'ов.
    sessions = list(
        (await db.scalars(select(Session).where(Session.user_id == user_a.id))).all()
    )
    assert sessions == []


async def test_hard_delete_cascades_pat(
    client, admin_token, user_a, db, notify_calls,
):
    """PAT юзера тоже сносятся (ORM-cascade)."""
    from src.models import PersonalAccessToken
    from src.utils.ids import _new_id

    pat = PersonalAccessToken(
        id=_new_id("pat_"),
        user_id=user_a.id,
        name="test",
        token_hash="x" * 64,
        token_prefix="abcd",
        allowed_services=["service_x"],
    )
    db.add(pat)
    await db.flush()
    pat_id = pat.id

    resp = await _hard_delete(client, admin_token, user_a.id)
    assert resp.status_code == 200

    surviving = await db.scalar(
        select(PersonalAccessToken).where(PersonalAccessToken.id == pat_id)
    )
    assert surviving is None


async def test_hard_delete_nonexistent_returns_404(
    client, admin_token, notify_calls,
):
    resp = await _hard_delete(client, admin_token, "usr_nonexistent")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "USER_NOT_FOUND"
    # secret_service notify НЕ должен вызываться при 404.
    assert notify_calls == []


async def test_dept_admin_cannot_hard_delete(
    client, dept_admin_a_token, user_a, notify_calls,
):
    """department_admin не имеет права на hard-delete (только account_admin)."""
    resp = await _hard_delete(client, dept_admin_a_token, user_a.id)
    assert resp.status_code == 403
    assert notify_calls == []


async def test_regular_user_cannot_hard_delete(
    client, user_a_token, user_b, notify_calls,
):
    resp = await _hard_delete(client, user_a_token, user_b.id)
    assert resp.status_code == 403
    assert notify_calls == []


async def test_hard_delete_last_account_admin_blocked(
    client, admin_token, account_admin, db, notify_calls,
):
    """Нельзя снести единственного account_admin'а — иначе платформа без admin'а."""
    # Session-level seeding в conftest._seed_e2e_admin создаёт e2e_admin
    # (account_admin) вне SAVEPOINT'а, поэтому в БД на момент теста сидят оба
    # активных админа: e2e_admin + t_admin. Чтобы получить «единственный
    # account_admin» сценарий — деактивируем e2e_admin в рамках SAVEPOINT'а
    # (откатится после teardown'а, не загрязняет последующие тесты).
    from src.core.constants import PlatformRole
    from src.models import User

    e2e_admins = list(
        (await db.scalars(
            select(User).where(
                User.platform_role == PlatformRole.ACCOUNT_ADMIN.value,
                User.username != account_admin.username,
            )
        )).all()
    )
    for u in e2e_admins:
        u.is_active = False
    await db.flush()
    await db.commit()

    resp = await _hard_delete(client, admin_token, account_admin.id)
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "LAST_ACCOUNT_ADMIN"
    # secret_service notify НЕ должен звучать на guard-rejection.
    assert notify_calls == []


async def test_hard_delete_one_of_two_account_admins_allowed(
    client, admin_token, account_admin, db, notify_calls,
):
    """Если account_admin'ов >=2, снос одного из них разрешён."""
    from src.core.constants import PlatformRole
    from src.core.security import hash_password
    from src.utils.ids import user_id

    second = User(
        id=user_id(),
        username="t_admin2",
        password_hash=hash_password("Admin12345678!"),
        platform_role=PlatformRole.ACCOUNT_ADMIN.value,
        department_id=None,
    )
    db.add(second)
    await db.flush()
    await db.commit()

    resp = await _hard_delete(client, admin_token, second.id, reason="role_review")
    assert resp.status_code == 200
    remaining = await db.scalar(select(User).where(User.id == second.id))
    assert remaining is None


async def test_hard_delete_requires_reason(client, admin_token, user_a, notify_calls):
    """`reason` обязателен — без него 422 от pydantic."""
    resp = await client.request(
        "DELETE",
        DELETE_URL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={},
    )
    assert resp.status_code == 422


async def test_hard_delete_empty_reason_rejected(
    client, admin_token, user_a, notify_calls,
):
    """Пустой `reason` (min_length=1) отклоняется."""
    resp = await _hard_delete(client, admin_token, user_a.id, reason="")
    assert resp.status_code == 422


async def test_secret_service_notify_failure_does_not_break_response(
    client, admin_token, user_a, db, monkeypatch,
):
    """Если secret_service отвалился — endpoint всё равно 200, юзер снесён."""

    async def _boom(user_id, actor_id, actor_username):
        raise RuntimeError("secret_service down")

    monkeypatch.setattr(secret_service_client, "notify_user_deleted", _boom)

    resp = await _hard_delete(client, admin_token, user_a.id)
    assert resp.status_code == 200
    remaining = await db.scalar(select(User).where(User.id == user_a.id))
    assert remaining is None
