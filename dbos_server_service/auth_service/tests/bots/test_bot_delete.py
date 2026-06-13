"""Тесты: DELETE /api/auth/v1/bots/{bot_id} — физическое удаление бота.

В отличие от мягкого disable (PATCH со status=disabled) этот endpoint сносит
строку `bot_accounts` вместе со всеми зависимыми записями (токены, service-роли,
членства в группах) каскадом. Доступ — только account_admin: department_admin
физически удалить бота не может даже в своём отделе.
"""

from datetime import timedelta

from sqlalchemy import select

from src.models.bot_account import BotAccount
from src.models.bot_service_role import BotServiceRole
from src.models.bot_token import BotToken
from src.services.audit_events import SERVICE_EVENTS
from src.utils.time import utcnow

from tests._helpers.http import _create_bot  # noqa: F401 — общий helper

BOTS_URL = "/api/auth/v1/bots"


async def _issue_token(client, token, bot_id, name="del_tok"):
    return await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )


def _delete(client, token, bot_id):
    return client.delete(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


# ── happy path ────────────────────────────────────────────────────────────────

async def test_account_admin_deletes_bot_and_cascades(
    client, admin_token, dept_a_with_service, service_x, db,
):
    """account_admin удаляет бота → 200; бот, его токены и роли исчезли из БД."""
    created = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="del_full_bot", services=[service_x.service_name],
    )
    bot_id = created.json()["bot_id"]

    tok_resp = await _issue_token(client, admin_token, bot_id)
    assert tok_resp.status_code == 201, tok_resp.text

    # навешиваем service-роль, чтобы проверить каскад bot_service_roles
    role_resp = await client.post(
        f"{BOTS_URL}/{bot_id}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )
    assert role_resp.status_code == 201, role_resp.text

    # sanity: записи реально есть до удаления
    assert (await db.scalars(select(BotToken).where(BotToken.bot_id == bot_id))).all()
    assert (await db.scalars(select(BotServiceRole).where(BotServiceRole.bot_id == bot_id))).all()

    resp = await _delete(client, admin_token, bot_id)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert await db.get(BotAccount, bot_id) is None
    assert (await db.scalars(select(BotToken).where(BotToken.bot_id == bot_id))).all() == []
    assert (await db.scalars(select(BotServiceRole).where(BotServiceRole.bot_id == bot_id))).all() == []


async def test_deleted_bot_not_listable(client, admin_token, dept_a, db):
    """После удаления бот пропадает из GET /bots."""
    created = await _create_bot(client, admin_token, dept_a.id, name="del_gone_bot")
    bot_id = created.json()["bot_id"]

    assert (await _delete(client, admin_token, bot_id)).status_code == 200

    resp = await client.get(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "BOT_NOT_FOUND"


# ── 404 ────────────────────────────────────────────────────────────────────────

async def test_delete_unknown_bot_404(client, admin_token):
    resp = await _delete(client, admin_token, "bot_does_not_exist")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error_code"] == "BOT_NOT_FOUND"


# ── authz ────────────────────────────────────────────────────────────────────

async def test_dept_admin_cannot_delete_own_dept_bot(
    client, admin_token, dept_admin_a_token, dept_a, db,
):
    """department_admin не может физически удалить бота даже своего отдела → 403."""
    created = await _create_bot(client, admin_token, dept_a.id, name="del_da_bot")
    bot_id = created.json()["bot_id"]

    resp = await _delete(client, dept_admin_a_token, bot_id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "BOT_DELETE_FORBIDDEN"

    # бот на месте — denial не должен ничего удалить
    db.expire_all()
    assert await db.get(BotAccount, bot_id) is not None


async def test_regular_user_cannot_delete_bot(
    client, admin_token, user_a_token, dept_a,
):
    """Обычный юзер → 403 (AnyAdmin/AccountAdmin-guard отбивает раньше сервиса)."""
    created = await _create_bot(client, admin_token, dept_a.id, name="del_user_bot")
    bot_id = created.json()["bot_id"]

    resp = await _delete(client, user_a_token, bot_id)
    assert resp.status_code == 403, resp.text


# ── audit catalog ──────────────────────────────────────────────────────────────

def test_bot_delete_event_severity_is_critical():
    """`bot.delete` объявлен в каталоге с severity CRITICAL."""
    ev = next(e for e in SERVICE_EVENTS if e["action"] == "bot.delete")
    assert ev["default_severity"] == "CRITICAL"
