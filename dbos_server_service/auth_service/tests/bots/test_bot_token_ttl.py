"""Bot-token TTL: дефолтный срок жизни — 6 месяцев (`BOT_TOKEN_TTL_SECONDS`).

После истечения introspect возвращает `active=False`, плюс эмитится отдельный
`bot.token_expired` audit-event (WARNING) — чтобы SIEM отличал просроченный
токен от revoke/inactive bot и мог детектить consumer'ов, не обновляющих токен.
"""

from datetime import timedelta

import pytest

from src.core.constants import BOT_TOKEN_TTL_SECONDS
from src.utils.time import utcnow

BOTS_URL = "/api/auth/v1/bots"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


def test_constant_value_is_six_months():
    """`BOT_TOKEN_TTL_SECONDS = 6 * 30 * 24 * 3600 = 15_552_000`."""
    assert BOT_TOKEN_TTL_SECONDS == 15_552_000
    assert BOT_TOKEN_TTL_SECONDS == 6 * 30 * 24 * 3600


async def test_create_bot_token_without_expires_at_defaults_to_six_months(
    client, admin_token, dept_a_with_service, service_x,
):
    """`POST /bots/{id}/tokens` без `expires_at` → expires_at = now + 6 мес."""
    create = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "ttl_default_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name],
        },
    )
    assert create.status_code == 201
    bot_id = create.json()["bot_id"]

    issued_before = utcnow()
    resp = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "default_ttl_token"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["expires_at"] is not None, (
        "bot-токен без явного expires_at должен получить дефолтный TTL, "
        "а не остаться бессрочным"
    )

    from datetime import datetime
    exp = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    expected = issued_before + timedelta(seconds=BOT_TOKEN_TTL_SECONDS)
    # допускаем небольшой дрейф между фиксацией issued_before и фактическим
    # `utcnow()` внутри сервиса (миграции/connection pool tickling).
    delta = abs((exp - expected).total_seconds())
    assert delta < 60, (
        f"expires_at ({exp}) должен отстоять от now ({issued_before}) "
        f"примерно на {BOT_TOKEN_TTL_SECONDS}s, drift={delta:.1f}s"
    )


async def test_create_bot_token_respects_explicit_expires_at(
    client, admin_token, dept_a_with_service, service_x,
):
    """Если caller передал явный future `expires_at` — берём именно его."""
    create = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "ttl_explicit_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name],
        },
    )
    assert create.status_code == 201
    bot_id = create.json()["bot_id"]

    explicit_exp = utcnow() + timedelta(days=14)
    resp = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "explicit_ttl_token",
            "expires_at": explicit_exp.isoformat(),
        },
    )
    assert resp.status_code == 201
    from datetime import datetime
    got = datetime.fromisoformat(resp.json()["expires_at"].replace("Z", "+00:00"))
    delta = abs((got - explicit_exp).total_seconds())
    assert delta < 2, f"явный expires_at должен быть сохранён 1-в-1, drift={delta:.3f}s"


async def test_expired_bot_token_introspect_returns_active_false(
    client, admin_token, dept_a_with_service, service_x, db,
):
    """Просроченный bot-токен → `active=False` через introspect."""
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.utils.ids import bot_id, bot_token_id

    bot = BotAccount(
        id=bot_id(),
        name="expired_token_bot",
        department_id=dept_a_with_service.id,
        allowed_services=[service_x.service_name],
        is_active=True,
        created_by="usr_placeholder",
    )
    db.add(bot)
    await db.flush()

    raw, prefix, token_hash = generate_bot_token()
    expired_at = utcnow() - timedelta(hours=1)
    token = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="already_expired",
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=expired_at,
    )
    db.add(token)
    await db.flush()
    await db.commit()

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.status_code == 200
    assert resp.json()["active"] is False


async def test_expired_bot_token_emits_bot_token_expired_audit(
    client, admin_token, dept_a_with_service, service_x, db, monkeypatch,
):
    """Просроченный bot-токен → audit `bot.token_expired` (WARNING).

    `token.introspect status=failure reason=expired` тоже эмитится (общий путь
    introspect-фейлов), плюс новый `bot.token_expired` — чтобы SIEM-rule мог
    детектить именно просроченные токены, а не любые introspect-фейлы.
    """
    from src.core.security import generate_bot_token
    from src.models import BotAccount, BotToken
    from src.services import audit_service as audit_mod
    from src.utils.ids import bot_id, bot_token_id

    bot = BotAccount(
        id=bot_id(),
        name="expired_audit_bot",
        department_id=dept_a_with_service.id,
        allowed_services=[service_x.service_name],
        is_active=True,
        created_by="usr_placeholder",
    )
    db.add(bot)
    await db.flush()

    raw, prefix, token_hash = generate_bot_token()
    expired_at = utcnow() - timedelta(minutes=5)
    tok = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="audit_expired",
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=expired_at,
    )
    db.add(tok)
    await db.flush()
    await db.commit()

    captured: list[dict] = []
    original_emit = audit_mod.emit

    def _capture(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original_emit(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _capture)

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.status_code == 200
    assert resp.json()["active"] is False

    expired_events = [e for e in captured if e["action"] == "bot.token_expired"]
    assert expired_events, (
        f"должен быть эмитнут bot.token_expired, "
        f"got: {[e['action'] for e in captured]}"
    )
    ev = expired_events[0]
    assert ev["actor_id"] == bot.id
    assert ev["details"]["bot_token_id"] == tok.id
    assert ev["details"]["error_code"] == "BOT_TOKEN_EXPIRED"
    assert ev["details"]["expires_at"].startswith(expired_at.isoformat()[:10])
