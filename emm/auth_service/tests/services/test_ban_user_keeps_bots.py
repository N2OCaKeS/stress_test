"""Регрессия политики `bots survive ban`.

Owner-decision (2026-06-01): `ban_user` НЕ должен автоматически отзывать
ботов, созданных юзером. Бот — отдельная identity отдела; бан владельца
не валит CI/integrations. Полный lockdown — вручную через
`DELETE /api/auth/v1/bots/{id}/tokens/{token_id}` или ожидание истечения
срока токена.

Покрытие:
- `ban_user` не дёргает `BotRepository.list_by_creator` /
  `BotTokenRepository.revoke_all_for_bots`.
- bot-токен остаётся живым после бана владельца (introspect → active).
- audit-details содержит `bots_policy="no_auto_revoke"`.
- manual revoke через DELETE /bots/{id}/tokens/{token_id} продолжает
  работать (т.е. инфраструктура отзыва не выпилена за компанию).
"""

from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.bots import BotRepository
from datetime import timedelta
from src.utils.time import utcnow

BAN_URL = "/api/auth/v1/users/{user_id}/ban"
BOTS_URL = "/api/auth/v1/bots"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def _ban(client, token, user_id, reason="ban regression test"):
    return await client.post(
        BAN_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"ban_type": "permanent", "reason": reason},
    )


async def _make_owned_bot_with_token(db, dept_id, owner_id, name):
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.utils.ids import bot_id, bot_token_id

    bot = BotAccount(
        id=bot_id(),
        name=name,
        department_id=dept_id,
        allowed_services=[],
        is_active=True,
        created_by=owner_id,
    )
    db.add(bot)
    await db.flush()
    raw, prefix, token_hash = generate_bot_token()
    token = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name=f"{name}_tok",
        token_hash=token_hash,
        token_prefix=prefix,
    )
    db.add(token)
    await db.flush()
    return bot, token, raw


async def test_ban_user_does_not_call_bot_revoke_chain(
    client, admin_token, user_a, dept_a, db, monkeypatch,
):
    """`ban_user` не трогает bot-repo / bot_token-repo revoke-методы."""
    counts = {"list_by_creator": 0, "revoke_all_for_bots": 0, "revoke": 0}

    orig_list_by_creator = BotRepository.list_by_creator
    orig_bulk = BotTokenRepository.revoke_all_for_bots
    orig_revoke = BotTokenRepository.revoke

    async def list_by_creator(self, *a, **k):
        counts["list_by_creator"] += 1
        return await orig_list_by_creator(self, *a, **k)

    async def revoke_all_for_bots(self, *a, **k):
        counts["revoke_all_for_bots"] += 1
        return await orig_bulk(self, *a, **k)

    async def revoke(self, *a, **k):
        counts["revoke"] += 1
        return await orig_revoke(self, *a, **k)

    monkeypatch.setattr(BotRepository, "list_by_creator", list_by_creator)
    monkeypatch.setattr(BotTokenRepository, "revoke_all_for_bots", revoke_all_for_bots)
    monkeypatch.setattr(BotTokenRepository, "revoke", revoke)

    # Юзер с несколькими ботами — чтобы заведомо был кандидат на revoke.
    for i in range(2):
        await _make_owned_bot_with_token(db, dept_a.id, user_a.id, f"survives_{i}")
    await db.commit()

    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 200

    assert counts["list_by_creator"] == 0, (
        "ban_user не должен искать ботов по created_by"
    )
    assert counts["revoke_all_for_bots"] == 0
    assert counts["revoke"] == 0


async def test_ban_user_leaves_owned_bot_tokens_active(
    client, admin_token, user_a, dept_a, db,
):
    """introspect bot-токена после ban'а владельца → active=True."""
    _, _, raw_token = await _make_owned_bot_with_token(
        db, dept_a.id, user_a.id, "owned_alive",
    )
    await db.commit()

    pre = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert pre.status_code == 200
    assert pre.json()["active"] is True

    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 200

    post = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert post.status_code == 200
    assert post.json()["active"] is True, (
        "bots survive ban: владелец забанен, бот живёт"
    )


async def test_ban_audit_marks_bots_policy_no_auto_revoke(
    client, admin_token, user_a, dept_a, db, monkeypatch,
):
    """`user.ban` audit-details должен явно нести `bots_policy=no_auto_revoke`."""
    from src.services import audit_service as audit_mod

    # Один бот + токен, чтобы счётчики были осмысленными (не «ботов не было
    # — поэтому ничего не отозвали»).
    await _make_owned_bot_with_token(db, dept_a.id, user_a.id, "policy_bot")
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
    assert ban_events, "ожидался ровно один `user.ban` audit"
    details = ban_events[0]["details"]
    assert details.get("bots_policy") == "no_auto_revoke"
    # Counter-поля сохранены для SIEM-совместимости, должны быть нулевые.
    assert details.get("bot_tokens_revoked") == 0
    assert details.get("owned_bots_count") == 0


async def test_manual_bot_token_revoke_still_works_after_owner_ban(
    client, admin_token, user_a, dept_a, db,
):
    """После ban'а владельца ручной revoke токена бота должен оставаться рабочим.

    Сценарий: dept_admin / account_admin отзывает токен через
    `DELETE /api/auth/v1/bots/{bot_id}/tokens/{token_id}` — путь не сломан
    тем, что мы убрали авто-revoke в `ban_user`.
    """
    # Создаём бот с токеном через API — берём bot_id и token_id из ответа.
    bot_create = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "manual_revoke_bot",
            "department_id": dept_a.id,
            "allowed_services": [],
        },
    )
    assert bot_create.status_code == 201
    bot_id = bot_create.json()["bot_id"]

    tok_resp = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "manual_revoke_tok", "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )
    assert tok_resp.status_code == 201, tok_resp.text
    tok_data = tok_resp.json()
    raw_token = tok_data["token"]
    token_id = tok_data["token_id"]

    # Бан владельца — токен живёт.
    ban_resp = await _ban(client, admin_token, user_a.id)
    assert ban_resp.status_code == 200

    intro_before = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert intro_before.json()["active"] is True

    # Ручной revoke — должен сработать.
    revoke_resp = await client.delete(
        f"{BOTS_URL}/{bot_id}/tokens/{token_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert revoke_resp.status_code == 200

    intro_after = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert intro_after.json()["active"] is False, (
        "manual revoke остался рабочим: токен бота после DELETE → active=False"
    )
