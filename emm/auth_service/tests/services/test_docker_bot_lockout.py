"""Lockout per-bot для `/docker/token` brute-force.

Счётчик `failed_token_attempts` + `locked_until`
на `BotAccount`. Симметрия с user-password lockout пути.

Identifying бота: бот ищется по `username` из Basic-auth (поле `bot.name`).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from tests._helpers.http import _basic  # noqa: F401 — общий helper
from datetime import timedelta
from src.utils.time import utcnow

TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
BOTS_URL = "/api/auth/v1/bots"


async def _enable_docker(client, token, dept_id):
    return await client.put(
        CONFIG_URL.format(dept_id=dept_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"pull_policy": "all", "pull_user_ids": [], "push_user_ids": []},
    )


async def _make_bot(client, admin_token, dept_id, name):
    bot_resp = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": []},
    )).json()
    tok_resp = (await client.post(
        f"{BOTS_URL}/{bot_resp['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "tok", "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )).json()
    return bot_resp["bot_id"], tok_resp["token"]


async def test_bot_lockout_after_max_attempts(client, admin_token, dept_a):
    """5 неверных bot-токенов с правильным bot.name → 6-й запрос (даже с
    корректным токеном) → 429 ACCOUNT_TEMPORARILY_LOCKED."""
    await _enable_docker(client, admin_token, dept_a.id)
    bot_name = "lockout_bot_basic"
    _, good_token = await _make_bot(client, admin_token, dept_a.id, bot_name)

    bad_token = "dbos_bot_completely_wrong_token_string"
    for _ in range(5):
        resp = await client.get(
            TOKEN_URL,
            headers=_basic(bot_name, bad_token),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

    resp = await client.get(
        TOKEN_URL,
        headers=_basic(bot_name, good_token),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"
    assert body["details"]["retry_after_seconds"] >= 0


async def test_bot_lockout_resets_on_success(client, admin_token, dept_a):
    """Успех с корректным токеном сбрасывает счётчик."""
    await _enable_docker(client, admin_token, dept_a.id)
    bot_name = "lockout_bot_reset"
    _, good_token = await _make_bot(client, admin_token, dept_a.id, bot_name)

    bad_token = "dbos_bot_wrong_zzz"
    for _ in range(4):
        await client.get(
            TOKEN_URL,
            headers=_basic(bot_name, bad_token),
            params={"service": "registry.test"},
        )

    resp = await client.get(
        TOKEN_URL,
        headers=_basic(bot_name, good_token),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 200, resp.text

    # Снова можно 4 фейла без lockout'а.
    for _ in range(4):
        resp = await client.get(
            TOKEN_URL,
            headers=_basic(bot_name, bad_token),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401

    resp = await client.get(
        TOKEN_URL,
        headers=_basic(bot_name, good_token),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 200, resp.text


async def test_bot_lockout_jit_release_after_expiry(client, admin_token, dept_a, db):
    """Истёкший `locked_until` снимается на следующем запросе."""
    await _enable_docker(client, admin_token, dept_a.id)
    bot_name = "lockout_bot_expiry"
    bot_id, good_token = await _make_bot(client, admin_token, dept_a.id, bot_name)

    bad_token = "dbos_bot_wrong_eee"
    for _ in range(5):
        await client.get(
            TOKEN_URL,
            headers=_basic(bot_name, bad_token),
            params={"service": "registry.test"},
        )

    from src.models.bot_account import BotAccount
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.execute(
        update(BotAccount)
        .where(BotAccount.id == bot_id)
        .values(locked_until=past)
    )
    await db.commit()

    resp = await client.get(
        TOKEN_URL,
        headers=_basic(bot_name, good_token),
        params={"service": "registry.test"},
    )
    assert resp.status_code == 200, resp.text


async def test_bot_lockout_unknown_bot_no_effect(client, admin_token, dept_a):
    """Username, который не соответствует ни одному боту → 401 без
    side-effect'ов (никаких записей не создаётся)."""
    await _enable_docker(client, admin_token, dept_a.id)

    bad_token = "dbos_bot_no_bot_xxx"
    for _ in range(10):
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("nonexistent_bot_xyz", bad_token),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"
