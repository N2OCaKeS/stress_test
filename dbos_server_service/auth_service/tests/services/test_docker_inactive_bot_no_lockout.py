"""Disabled бот не получает инкремент failed_token_attempts.

Иначе любой запрос с правильным `username` и любым токеном забивает счётчик,
и после реактивации бот сразу падает под lockout. Фейл всё равно возвращается,
но без побочек на counter / locked_until.
"""

import base64

from sqlalchemy import select, update

from src.models import BotAccount


TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
BOTS_URL = "/api/auth/v1/bots"


def _basic(username, password):
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


async def _enable_docker(client, token, dept_id):
    return await client.put(
        CONFIG_URL.format(dept_id=dept_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"pull_policy": "all", "pull_user_ids": [], "push_user_ids": []},
    )


async def test_inactive_bot_failure_skips_lockout_counter(
    client, db, admin_token, dept_a,
):
    await _enable_docker(client, admin_token, dept_a.id)

    create = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "inactive_bot", "department_id": dept_a.id, "allowed_services": []},
    )
    assert create.status_code == 201, create.text
    bot_id = create.json()["bot_id"]

    # Отключаем бота напрямую в БД.
    await db.execute(
        update(BotAccount).where(BotAccount.id == bot_id).values(
            is_active=False, status="disabled",
        )
    )
    await db.commit()

    # Несколько попыток с битым токеном по username отключённого бота.
    for _ in range(3):
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("inactive_bot", "dbos_bot_garbage"),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

    row = await db.scalar(select(BotAccount).where(BotAccount.id == bot_id))
    await db.refresh(row)
    assert row.failed_token_attempts == 0, (
        f"inactive бот не должен набирать failed_token_attempts, got {row.failed_token_attempts}"
    )
    assert row.locked_until is None
