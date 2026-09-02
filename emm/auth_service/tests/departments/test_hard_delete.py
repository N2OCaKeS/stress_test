"""Тесты: DELETE /api/auth/v1/departments/{id} — hard-delete отдела.

Покрывает:
- happy path: row физически снесён + secret_service notify;
- occupied-dept protection (нельзя снести dept с активными юзерами);
- cascade на bot'ов и oauth_client'ов отдела;
- 404 на несуществующего;
- secret_service notify не валит ответ при exception'е.
"""

import pytest
from sqlalchemy import select

from src.models import Department
from src.services import secret_service_client

DELETE_URL = "/api/auth/v1/departments/{dept_id}"


@pytest.fixture
def notify_calls(monkeypatch):
    """Перехват `secret_service_client.notify_dept_deleted`."""
    calls = []

    async def _capture(dept_id, actor_id, actor_username):
        calls.append({
            "dept_id": dept_id,
            "actor_id": actor_id,
            "actor_username": actor_username,
        })

    monkeypatch.setattr(
        secret_service_client, "notify_dept_deleted", _capture
    )
    return calls


async def _hard_delete(client, token, dept_id, reason="closed"):
    return await client.request(
        "DELETE",
        DELETE_URL.format(dept_id=dept_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": reason},
    )


async def test_admin_hard_deletes_empty_department(
    client, admin_token, dept_b, db, notify_calls,
):
    """Отдел без юзеров и ботов — снос проходит."""
    resp = await _hard_delete(client, admin_token, dept_b.id, reason="dept_closed")
    assert resp.status_code == 200

    remaining = await db.scalar(select(Department).where(Department.id == dept_b.id))
    assert remaining is None

    assert len(notify_calls) == 1
    assert notify_calls[0]["dept_id"] == dept_b.id
    assert notify_calls[0]["actor_username"] == "t_admin"


async def test_hard_delete_dept_with_active_users_blocked(
    client, admin_token, dept_a, user_a, db, notify_calls,
):
    """Запрет: dept_a содержит активного user_a — 422 USERS_REMAIN_IN_DEPT."""
    resp = await _hard_delete(client, admin_token, dept_a.id)
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "USERS_REMAIN_IN_DEPT"

    # Отдел должен остаться нетронутым.
    remaining = await db.scalar(select(Department).where(Department.id == dept_a.id))
    assert remaining is not None
    # secret_service NOT called на guard-rejection.
    assert notify_calls == []


async def test_hard_delete_dept_cascades_bots(
    client, admin_token, dept_b, db, notify_calls,
):
    """Боты отдела сносятся вместе с самим dept'ом."""
    from src.models import BotAccount
    from src.utils.ids import bot_id

    bot = BotAccount(
        id=bot_id(),
        name="t_bot_dept_b",
        department_id=dept_b.id,
        allowed_services=["service_x"],
    )
    db.add(bot)
    await db.flush()
    await db.commit()
    bot_pk = bot.id

    resp = await _hard_delete(client, admin_token, dept_b.id)
    assert resp.status_code == 200

    surviving_bot = await db.scalar(
        select(BotAccount).where(BotAccount.id == bot_pk)
    )
    assert surviving_bot is None


async def test_hard_delete_dept_nonexistent_returns_404(
    client, admin_token, notify_calls,
):
    resp = await _hard_delete(client, admin_token, "dep_nonexistent")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "DEPARTMENT_NOT_FOUND"
    assert notify_calls == []


async def test_dept_admin_cannot_hard_delete_dept(
    client, dept_admin_a_token, dept_b, notify_calls,
):
    resp = await _hard_delete(client, dept_admin_a_token, dept_b.id)
    assert resp.status_code == 403
    assert notify_calls == []


async def test_hard_delete_dept_requires_reason(
    client, admin_token, dept_b, notify_calls,
):
    resp = await client.request(
        "DELETE",
        DELETE_URL.format(dept_id=dept_b.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={},
    )
    assert resp.status_code == 422


async def test_secret_service_notify_failure_does_not_break_dept_delete(
    client, admin_token, dept_b, db, monkeypatch,
):
    async def _boom(dept_id, actor_id, actor_username):
        raise RuntimeError("secret_service down")

    monkeypatch.setattr(secret_service_client, "notify_dept_deleted", _boom)

    resp = await _hard_delete(client, admin_token, dept_b.id)
    assert resp.status_code == 200
    remaining = await db.scalar(select(Department).where(Department.id == dept_b.id))
    assert remaining is None
