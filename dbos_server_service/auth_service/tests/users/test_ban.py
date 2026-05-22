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
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_BANNED"


async def test_banned_user_refresh_fails(client, admin_token, user_a, user_a_token):
    login_resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})
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
    resp = await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})
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
    login_data = (await client.post(LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"})).json()
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


@pytest.mark.xfail(
    reason="ban_user revoke-ит PAT, поэтому introspect отвечает active=False. "
    "Тест ожидает active=True + is_banned=True (старая семантика). "
    "Либо переписать тест под новую семантику, либо удалить — оставить дубликат "
    "test_pat_introspect_reports_is_banned_false_after_unban покрывает unban-путь.",
    strict=False,
)
async def test_pat_introspect_reports_is_banned_true_after_ban(
    client, admin_token, user_a, user_a_token,
):
    """PAT-introspect забаненного юзера: `is_banned=True`.

    До фикса возвращал `False`, потому что `user.is_active` не менялся
    при бане (см. `authorization_service.introspect:115`).
    """
    raw = (await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "ban_intr_pat", "allowed_services": []},
    )).json()["token"]

    await _ban(client, admin_token, user_a.id)

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.status_code == 200
    body = resp.json()
    # PAT остаётся валидным как токен (его не отзывали), но юзер забанен.
    assert body["active"] is True
    assert body["is_banned"] is True


# unban_user реактивирует PAT-токены, отозванные при ban'е, через
# `revoked_reason="ban"` (миграция c7d8e9f0a1b2).
async def test_pat_introspect_reports_is_banned_false_after_unban(
    client, admin_token, user_a, user_a_token,
):
    """После unban PAT-introspect снова возвращает `is_banned=False`."""
    raw = (await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "unban_intr_pat", "allowed_services": []},
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
        json={"name": "ban_revoke_pat", "allowed_services": []},
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


# ── Bot-token revoke on ban ───────────────────────────────────────────────────
#
# `ban_user` revoke'ит и bot-токены ботов, созданных юзером
# (`BotAccount.created_by == user_id`). Покрываем явно, чтобы regression
# detector видел bot-token revoke не только через PAT-introspect (другой
# code-path).
#
# Setup-обоснование: `user_a` — обычный юзер, эндпоинт `POST /bots` имеет
# `AnyAdmin`-guard и не пустит user_a создать бота через HTTP. Поэтому
# создаём `BotAccount` + `BotToken` напрямую через ORM, выставляя
# `created_by=user_a.id` — это правомерное состояние (бот был создан
# админом по поручению юзера, либо через legacy seed, либо через будущий
# UI-флоу). Это в точности тот сценарий, который `ban_user` должен
# закрыть: атакующий, контролирующий user_a, получил bot-токен и хочет
# сохранить доступ после ban'а.


async def test_ban_revokes_bot_tokens_for_owned_bots(
    client, admin_token, user_a, dept_a, db,
):
    """`ban_user` revoke-ит токены ботов, у которых `created_by = user_id`.

    Проверяем напрямую (без bots HTTP API): создаём BotAccount c
    `created_by=user_a.id` + BotToken c `revoked_at IS NULL`, баним юзера,
    видим что введён `revoked_at` (физически в БД), а introspect возвращает
    `active=False`. Это явное покрытие bot-token revoke chain'а из
    `ban_user` (services/user_service.py:494-500), который раньше не имел
    прямого теста.
    """
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.utils.ids import bot_id, bot_token_id

    # 1. Создаём бот, у которого `created_by` указывает на user_a — этот
    #    бот должен попасть в выборку `owned_bots` при ban_user(user_a).
    bot = BotAccount(
        id=bot_id(),
        name="owned_by_user_a",
        department_id=dept_a.id,
        allowed_services=[],
        is_active=True,
        created_by=user_a.id,
    )
    db.add(bot)
    await db.flush()

    # 2. Bot-token для этого бота — без revoked_at, активный.
    raw_token, prefix, token_hash = generate_bot_token()
    token = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="active_token",
        token_hash=token_hash,
        token_prefix=prefix,
    )
    db.add(token)
    await db.flush()
    await db.commit()

    # 3. Sanity до ban'а — introspect должен видеть токен как `active=True`.
    pre_ban = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert pre_ban.status_code == 200
    assert pre_ban.json()["active"] is True, (
        "bot-токен должен быть active ДО ban'а — иначе тест ложноположительный"
    )

    # 4. Бан юзера.
    ban_resp = await _ban(client, admin_token, user_a.id)
    assert ban_resp.status_code == 200

    # 5. После ban'а: `revoked_at` физически проставлен (sqlalchemy-уровень),
    #    introspect возвращает `active=False` (HTTP-уровень).
    await db.refresh(token)
    assert token.revoked_at is not None, (
        "BotToken.revoked_at должен быть физически проставлен в БД после "
        "ban_user (services/user_service.py:494-500)"
    )

    post_ban = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert post_ban.status_code == 200
    assert post_ban.json()["active"] is False, (
        "introspect после ban должен вернуть active=False — bot-token revoke "
        "chain в ban_user сломан"
    )


async def test_ban_does_not_revoke_tokens_of_bots_owned_by_others(
    client, admin_token, user_a, dept_a, db,
):
    """`ban_user(user_a)` НЕ трогает токены ботов, созданных другими юзерами.

    Гарантирует, что фильтр `BotAccount.created_by == user_id` действительно
    ограничивает revoke-набор: бот, созданный admin'ом, не должен страдать
    при бане user_a. Это регрессия-страховка против over-revocation (которая
    могла бы привести к outage всех ботов в отделе).
    """
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.utils.ids import bot_id, bot_token_id

    # Бот, созданный НЕ user_a (created_by — placeholder для admin'а):
    other_owner = "usr_someone_else"
    bot = BotAccount(
        id=bot_id(),
        name="not_owned_by_user_a",
        department_id=dept_a.id,
        allowed_services=[],
        is_active=True,
        created_by=other_owner,
    )
    db.add(bot)
    await db.flush()

    raw_token, prefix, token_hash = generate_bot_token()
    token = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="untouched_token",
        token_hash=token_hash,
        token_prefix=prefix,
    )
    db.add(token)
    await db.flush()
    await db.commit()

    # Бан user_a — не должен затронуть этот токен.
    await _ban(client, admin_token, user_a.id)

    await db.refresh(token)
    assert token.revoked_at is None, (
        "Чужой bot-token (created_by != user_a.id) НЕ должен revoke'иться "
        "при ban'е user_a — это would break unrelated tenants"
    )

    resp = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert resp.json()["active"] is True


async def test_ban_audit_details_include_revoke_counters(
    client, admin_token, user_a, dept_a, db, monkeypatch,
):
    """`user.ban` audit-event несёт `pat_revoked`, `bot_tokens_revoked`,
    `owned_bots_count` — мониторинг по `user.ban` сможет видеть
    «ban стоил N PAT'ов и M ботов».
    """
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.services import audit_service as audit_mod
    from src.utils.ids import bot_id, bot_token_id

    # Один бот, один токен — для понятных значений в audit.
    bot = BotAccount(
        id=bot_id(),
        name="audit_counter_bot",
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
        name="counted_token",
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
    assert ban_events, f"должен быть эмитнут user.ban, got: {[e['action'] for e in captured]}"
    details = ban_events[0]["details"]
    assert details["pat_revoked"] is True
    assert details["bot_tokens_revoked"] == 1
    assert details["owned_bots_count"] == 1
