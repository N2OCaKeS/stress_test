"""Тесты: POST /api/auth/v1/users/{id}/ban и /unban — блокировка и разблокировка пользователей."""

import pytest

from src.core.constants import UserStatus
from src.models import User

BAN_URL = "/api/auth/v1/users/{user_id}/ban"
UNBAN_URL = "/api/auth/v1/users/{user_id}/unban"
LOGIN_URL = "/api/auth/v1/login"
TOKENS_URL = "/api/auth/v1/tokens"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def _ban(client, token, user_id, reason="test ban"):
    # `ban_type="permanent"` — `BanRequest` валидирует ban_type через
    # `Literal[BanType]`, прежний `"manual"` больше не проходит 422-проверку.
    return await client.post(
        BAN_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"ban_type": "permanent", "reason": reason},
    )


async def test_admin_bans_user(client, admin_token, user_a):
    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 200


async def test_banned_user_cannot_login(client, admin_token, user_a):
    await _ban(client, admin_token, user_a.id)
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BANNED"


async def test_banned_user_refresh_fails(client, admin_token, user_a, user_a_token):
    login_resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"})
    refresh_token = login_resp.json()["refresh_token"]
    await _ban(client, admin_token, user_a.id)
    resp = await client.post("/api/auth/v1/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code in (401, 403)


async def test_duplicate_ban_returns_409(client, admin_token, user_a):
    await _ban(client, admin_token, user_a.id)
    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "BAN_ALREADY_ACTIVE"


async def test_unban_allows_login(client, admin_token, user_a):
    await _ban(client, admin_token, user_a.id)
    await client.post(UNBAN_URL.format(user_id=user_a.id), headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"})
    assert resp.status_code == 200


async def test_unban_no_active_ban_returns_404(client, admin_token, user_a):
    resp = await client.post(UNBAN_URL.format(user_id=user_a.id), headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 404


async def test_dept_admin_a_cannot_ban_user_in_dept_b(client, dept_admin_a_token, user_b):
    resp = await _ban(client, dept_admin_a_token, user_b.id)
    assert resp.status_code == 403


async def test_regular_user_cannot_ban(client, user_a_token, user_b):
    resp = await _ban(client, user_a_token, user_b.id)
    assert resp.status_code == 403


async def test_ban_nonexistent_user_returns_404(client, admin_token):
    resp = await _ban(client, admin_token, "usr_nonexistent")
    assert resp.status_code == 404


async def test_ban_revokes_active_sessions(client, admin_token, user_a):
    login_data = (await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User12345678!"})).json()
    await _ban(client, admin_token, user_a.id)
    resp = await client.post("/api/auth/v1/refresh", json={"refresh_token": login_data["refresh_token"]})
    assert resp.status_code in (401, 403)


# ── is_active <-> status sync ─────────────────────────────────────────────────
#
# Раньше `ban_user`/`unban_user` обновляли только `User.status`, а поле
# `is_active` оставалось `True` навсегда. Из-за этого:
#   1. `authorization_service.introspect` для PAT возвращал `is_banned=False`
#      даже для забаненного юзера (`is_banned=not user.is_active`).
#   2. `repositories/users.list_all`/`list_by_department` фильтровали по
#      `is_active.is_(True)` — забаненные юзеры всё равно появлялись в списках.
# Фикс: синхронизировать оба поля (вариант A, аналогично `bot_service`).


async def test_ban_sets_is_active_false_in_db(client, admin_token, user_a, db):
    """После `ban_user` колонка `users.is_active` физически = False."""
    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 200

    # Refresh ORM-инстанс из БД, чтобы убедиться что коммит дошёл до колонки,
    # а не остался только в Python-объекте handler'а.
    await db.refresh(user_a)
    assert user_a.status == UserStatus.BANNED
    assert user_a.is_active is False


async def test_unban_sets_is_active_true_in_db(client, admin_token, user_a, db):
    """После `unban_user` колонка `users.is_active` снова = True."""
    await _ban(client, admin_token, user_a.id)
    unban_resp = await client.post(
        UNBAN_URL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert unban_resp.status_code == 200

    await db.refresh(user_a)
    assert user_a.status == UserStatus.ACTIVE
    assert user_a.is_active is True


# unban_user реактивирует PAT-токены, отозванные при ban'е, через
# `revoked_reason="ban"` (миграция c7d8e9f0a1b2).
async def test_pat_introspect_reports_is_banned_false_after_unban(
    client, admin_token, user_a, user_a_token,
):
    """После unban PAT-introspect снова возвращает `is_banned=False`."""
    raw = (await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "unban_intr_pat", "allowed_services": ["service_x"]},
    )).json()["token"]

    await _ban(client, admin_token, user_a.id)
    await client.post(
        UNBAN_URL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["is_banned"] is False


# ── PAT revoke on ban ─────────────────────────────────────────────────────────
#
# Раньше `ban_user` вызывал только `session_repo.revoke_all_for_user`, а
# `token_repo.revoke_all_for_user` НЕ вызывал. Активные PAT забаненного юзера
# продолжали валидироваться через `get_active_by_hash` (фильтр по
# `revoked_at IS NULL`), introspect возвращал `active=True, is_banned=True`,
# но это не мешало сервисам, которые проверяют только `active`. Атакующий со
# скомпрометированным PAT сохранял доступ. Фикс: revoke PAT внутри `ban_user`.

async def test_ban_revokes_active_pat(
    client, admin_token, user_a, user_a_token,
):
    """PAT, созданный до ban'а, после ban'а → introspect возвращает `active=False`.

    Раньше PAT оставался `active=True` (только `is_banned=True`), теперь
    `revoked_at` физически проставлен, и `get_active_by_hash` его не находит.
    """
    raw = (await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "ban_revoke_pat", "allowed_services": ["service_x"]},
    )).json()["token"]

    # PAT валиден ДО ban'а — sanity-чек, чтобы исключить ложноположительный
    # результат теста (PAT мог стать `active=False` по другой причине).
    pre_ban = await client.post(INTROSPECT_URL, json={"token": raw})
    assert pre_ban.status_code == 200
    assert pre_ban.json()["active"] is True

    await _ban(client, admin_token, user_a.id)

    post_ban = await client.post(INTROSPECT_URL, json={"token": raw})
    assert post_ban.status_code == 200
    assert post_ban.json()["active"] is False


# ── Bot-token survives ban (by-design) ────────────────────────────────────────
#
# Поведение поменялось: `ban_user` больше НЕ revoke'ит токены ботов, созданных
# забаненным юзером. Бот привязан к отделу и продолжает работать после ban'а
# своего создателя — иначе ban одного человека валит CI/integrations всего
# отдела. Если нужен полный lockdown — dept_admin перевыпустит токен через
# `POST /bots/{id}/tokens` (или revoke через `DELETE /bots/{id}/tokens/{tid}`).


async def test_ban_does_not_revoke_bot_tokens_of_owned_bots(
    client, admin_token, user_a, dept_a, db,
):
    """`ban_user` оставляет токены ботов с `created_by=user_id` живыми.

    Бот — отдельная identity. Создатель забанен → бот по-прежнему работает,
    introspect возвращает `active=True`. Это by-design.
    """
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.utils.ids import bot_id, bot_token_id

    bot = BotAccount(
        id=bot_id(),
        name="owned_by_user_a_survives",
        department_id=dept_a.id,
        allowed_services=[],
        is_active=True,
        created_by=user_a.id,
    )
    db.add(bot)
    await db.flush()

    raw_token, prefix, token_hash = generate_bot_token()
    token = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="survives_ban",
        token_hash=token_hash,
        token_prefix=prefix,
    )
    db.add(token)
    await db.flush()
    await db.commit()

    pre_ban = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert pre_ban.status_code == 200
    assert pre_ban.json()["active"] is True

    ban_resp = await _ban(client, admin_token, user_a.id)
    assert ban_resp.status_code == 200

    await db.refresh(token)
    assert token.revoked_at is None, (
        "by-design: bots survive ban — токен бота не должен быть отозван при "
        "бане его создателя"
    )

    post_ban = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert post_ban.status_code == 200
    assert post_ban.json()["active"] is True, (
        "введение ban_user не должно валить ботов — bot.is_active остался True"
    )


async def test_ban_audit_reports_zero_bot_revokes(
    client, admin_token, user_a, dept_a, db, monkeypatch,
):
    """`user.ban` audit оставляет `bot_tokens_revoked=0`/`owned_bots_count=0`.

    Поля сохранены для обратной совместимости SIEM-правил; значения теперь
    всегда нулевые — bots survive ban by design.
    """
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.services import audit_service as audit_mod
    from src.utils.ids import bot_id, bot_token_id

    bot = BotAccount(
        id=bot_id(),
        name="audit_zero_bot",
        department_id=dept_a.id,
        allowed_services=[],
        is_active=True,
        created_by=user_a.id,
    )
    db.add(bot)
    await db.flush()
    _, prefix, token_hash = generate_bot_token()
    token = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="zero_counted",
        token_hash=token_hash,
        token_prefix=prefix,
    )
    db.add(token)
    await db.flush()
    await db.commit()

    captured: list[dict] = []
    original_emit = audit_mod.emit

    def _capture(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original_emit(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _capture)

    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 200

    ban_events = [e for e in captured if e["action"] == "user.ban"]
    assert len(ban_events) == 1
    details = ban_events[0]["details"]
    assert details["pat_revoked"] is True
    assert details["bot_tokens_revoked"] == 0
    assert details["owned_bots_count"] == 0
    assert details["bots_policy"] == "no_auto_revoke"
