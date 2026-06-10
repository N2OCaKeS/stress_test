"""`token.touch`/`bot_token.touch` коммитится отдельно от revalidate/track_ip.

До фикса `last_used_at` коммитился вместе с следующими read'ами/write'ами:
если `collect_*_permissions` / `track_bot_ip` поднимали исключение,
rollback в `get_db()` вытирал touch. Аутентификация прошла, а «недавно
использован» в админке не обновлялся.

Симуляция: после `touch` имитируем падение downstream-операции через
monkeypatch и проверяем, что `last_used_at` в БД всё равно проставлен.
"""

import pytest
from sqlalchemy import select

from src.models import BotToken, PersonalAccessToken
from src.core.security import hash_opaque_token
from datetime import timedelta
from src.utils.time import utcnow


INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
TOKENS_URL = "/api/auth/v1/tokens"
BOTS_URL = "/api/auth/v1/bots"


async def test_pat_touch_persists_when_downstream_collect_fails(
    client, user_a, user_a_token, db, monkeypatch,
):
    create = await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "touch_pat", "allowed_services": ["service_x"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )
    assert create.status_code == 201, create.text
    raw = create.json()["token"]

    # Запоминаем исходное значение `last_used_at` (None на свежевыданный PAT).
    pat_row = await db.scalar(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == hash_opaque_token(raw),
        )
    )
    assert pat_row is not None
    assert pat_row.last_used_at is None

    # Подменяем `collect_user_permissions` чтобы он падал — touch уже должен
    # быть закоммичен на этот момент.
    from src.services import authorization_service as az

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated downstream failure")

    monkeypatch.setattr(az, "collect_user_permissions", _boom)

    # ASGITransport(raise_app_exceptions=True) пробрасывает unhandled-исключение
    # наружу, не превращает в 500. Главное — что touch уже закоммичен до краша.
    with pytest.raises(RuntimeError, match="simulated downstream failure"):
        await client.post(INTROSPECT_URL, json={"token": raw})

    db.expire_all()
    pat_row = await db.scalar(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == hash_opaque_token(raw),
        )
    )
    assert pat_row is not None
    assert pat_row.last_used_at is not None, (
        "touch должен быть закоммичен отдельной транзакцией до краша downstream"
    )


async def test_bot_token_touch_persists_when_track_bot_ip_fails(
    client, admin_token, dept_a, db, monkeypatch,
):
    bot = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "touch_bot", "department_id": dept_a.id, "allowed_services": []},
    )
    assert bot.status_code == 201, bot.text
    bot_id = bot.json()["bot_id"]

    tok = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "touch_bot_tok", "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )
    assert tok.status_code == 201, tok.text
    raw = tok.json()["token"]

    bt_row = await db.scalar(
        select(BotToken).where(BotToken.token_hash == hash_opaque_token(raw))
    )
    assert bt_row is not None
    assert bt_row.last_used_at is None

    # Подменяем `track_bot_ip` (lazy-imported в introspect) чтобы он падал.
    from src.services import bot_ip_tracker

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated track_bot_ip failure")

    monkeypatch.setattr(bot_ip_tracker, "track_bot_ip", _boom)

    with pytest.raises(RuntimeError, match="simulated track_bot_ip failure"):
        await client.post(INTROSPECT_URL, json={"token": raw})

    db.expire_all()
    bt_row = await db.scalar(
        select(BotToken).where(BotToken.token_hash == hash_opaque_token(raw))
    )
    assert bt_row is not None
    assert bt_row.last_used_at is not None, (
        "bot-token touch должен быть закоммичен отдельной транзакцией до track_bot_ip"
    )
