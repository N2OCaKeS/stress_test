"""Bot-token introspect: `touch(bot_token)` идёт ПОСЛЕ revalidate'а бота.

Симметрично PAT-ветке (`authorization_service` → ветка PAT: `touch(pat)`
после `is_active`/`status` check). Если бот inactive, introspect возвращает
`active=False` — обновлять `last_used_at` нет смысла, статистика «недавно
использован» в админке не должна реагировать на отбитые запросы.
"""

from datetime import timedelta

from src.core.security import generate_bot_token
from src.models import BotAccount, BotToken
from src.utils.ids import bot_id, bot_token_id
from src.utils.time import utcnow

INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def test_inactive_bot_introspect_does_not_touch_token(
    client, dept_a_with_service, service_x, db,
):
    bot = BotAccount(
        id=bot_id(),
        name="touch_skip_bot",
        department_id=dept_a_with_service.id,
        allowed_services=[service_x.service_name],
        is_active=False,
        created_by="usr_placeholder",
    )
    db.add(bot)
    await db.flush()

    raw, prefix, token_hash = generate_bot_token()
    tok = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="touch_skip_token",
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=utcnow() + timedelta(days=30),
    )
    db.add(tok)
    await db.flush()
    await db.commit()

    assert tok.last_used_at is None

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.status_code == 200
    assert resp.json()["active"] is False

    await db.refresh(tok)
    assert tok.last_used_at is None, (
        "last_used_at для inactive-бота не должен апдейтиться "
        f"(touch до revalidate'а), got {tok.last_used_at}"
    )


async def test_active_bot_introspect_does_touch_token(
    client, dept_a_with_service, service_x, db,
):
    """Контр-кейс: для активного бота `last_used_at` обновляется."""
    bot = BotAccount(
        id=bot_id(),
        name="touch_active_bot",
        department_id=dept_a_with_service.id,
        allowed_services=[service_x.service_name],
        is_active=True,
        created_by="usr_placeholder",
    )
    db.add(bot)
    await db.flush()

    raw, prefix, token_hash = generate_bot_token()
    tok = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="touch_active_token",
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=utcnow() + timedelta(days=30),
    )
    db.add(tok)
    await db.flush()
    await db.commit()

    assert tok.last_used_at is None

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.status_code == 200
    assert resp.json()["active"] is True

    await db.refresh(tok)
    assert tok.last_used_at is not None
