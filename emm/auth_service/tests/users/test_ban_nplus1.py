"""`ban_user` не делает лишних запросов по bot-токенам.

Поведение поменялось: bots survive ban by design — `ban_user` больше НЕ
вызывает ни `list_by_creator`, ни `revoke_all_for_bots`. Тесты проверяют
именно это (отсутствие лишних touch'ей таблицы ботов при ban'е).
"""

import pytest

from src.repositories.bot_tokens import BotTokenRepository
from src.repositories.bots import BotRepository

BAN_URL = "/api/auth/v1/users/{user_id}/ban"


async def _ban(client, token, user_id):
    return await client.post(
        BAN_URL.format(user_id=user_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"ban_type": "permanent", "reason": "nplus1 test"},
    )


@pytest.fixture()
def spy_repos(monkeypatch):
    """Считает вызовы repo-методов, относящихся к bot-revoke chain'у."""
    counts = {
        "list_all": 0,
        "list_by_creator": 0,
        "list_for_bot": 0,
        "revoke": 0,
        "revoke_all_for_bots": 0,
    }

    orig_list_all = BotRepository.list_all
    orig_list_by_creator = BotRepository.list_by_creator
    orig_list_for_bot = BotTokenRepository.list_for_bot
    orig_revoke = BotTokenRepository.revoke
    orig_bulk = BotTokenRepository.revoke_all_for_bots

    async def list_all(self, *a, **k):
        counts["list_all"] += 1
        return await orig_list_all(self, *a, **k)

    async def list_by_creator(self, *a, **k):
        counts["list_by_creator"] += 1
        return await orig_list_by_creator(self, *a, **k)

    async def list_for_bot(self, *a, **k):
        counts["list_for_bot"] += 1
        return await orig_list_for_bot(self, *a, **k)

    async def revoke(self, *a, **k):
        counts["revoke"] += 1
        return await orig_revoke(self, *a, **k)

    async def revoke_all_for_bots(self, *a, **k):
        counts["revoke_all_for_bots"] += 1
        return await orig_bulk(self, *a, **k)

    monkeypatch.setattr(BotRepository, "list_all", list_all)
    monkeypatch.setattr(BotRepository, "list_by_creator", list_by_creator)
    monkeypatch.setattr(BotTokenRepository, "list_for_bot", list_for_bot)
    monkeypatch.setattr(BotTokenRepository, "revoke", revoke)
    monkeypatch.setattr(BotTokenRepository, "revoke_all_for_bots", revoke_all_for_bots)
    return counts


async def _make_owned_bot_with_token(db, dept_id, owner_id, name):
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.utils.ids import bot_id, bot_token_id

    bot = BotAccount(
        id=bot_id(), name=name, department_id=dept_id,
        allowed_services=[], is_active=True, created_by=owner_id,
    )
    db.add(bot)
    await db.flush()
    _, prefix, token_hash = generate_bot_token()
    token = BotToken(
        id=bot_token_id(), bot_id=bot.id, name=f"{name}_tok",
        token_hash=token_hash, token_prefix=prefix,
    )
    db.add(token)
    await db.flush()
    return bot, token


async def test_ban_does_not_touch_bot_tables_when_owner_has_bots(
    spy_repos, client, admin_token, user_a, dept_a, db,
):
    """Несколько ботов с токенами → ноль вызовов bot-repo: bots survive ban."""
    for i in range(3):
        await _make_owned_bot_with_token(db, dept_a.id, user_a.id, f"owned_{i}")
    await db.commit()

    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 200

    assert spy_repos["list_by_creator"] == 0, (
        "by-design: bots survive ban — выборка по created_by не нужна"
    )
    assert spy_repos["list_all"] == 0
    assert spy_repos["list_for_bot"] == 0
    assert spy_repos["revoke"] == 0
    assert spy_repos["revoke_all_for_bots"] == 0


async def test_ban_does_not_touch_bot_tables_when_owner_has_no_bots(
    spy_repos, client, admin_token, user_a, db,
):
    """Без ботов: ban_user также не должен трогать таблицу ботов."""
    resp = await _ban(client, admin_token, user_a.id)
    assert resp.status_code == 200
    assert spy_repos["list_by_creator"] == 0
    assert spy_repos["list_all"] == 0
    assert spy_repos["revoke_all_for_bots"] == 0
