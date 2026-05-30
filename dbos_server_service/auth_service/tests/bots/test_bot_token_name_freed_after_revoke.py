"""Bot-токен: имя освобождается после revoke.

exists_name(bot_id, name) фильтрует по `revoked_at IS NULL` — после явного
revoke'а имя можно использовать повторно. Это штатный flow ротации
(dept_admin revoke → mint с тем же именем раз в полгода).

Аналог `test_pat_recreate_after_revoke.py` для bot-токенов.
Тесты на пересоздание помечены xfail: `uq_bot_token_name` в БД — не partial
unique, блокирует даже revoked-строки. src-сторона (exists_name) корректна,
нужна миграция UNIQUE (bot_id, name) WHERE revoked_at IS NULL.
"""

import pytest

BOTS_URL = "/api/auth/v1/bots"


async def _make_bot(client, admin_token, dept_id, name="rn_bot"):
    resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": []},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["bot_id"]


async def _mint_token(client, admin_token, bot_id, name):
    return await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name},
    )


async def _revoke_token(client, admin_token, bot_id, token_id):
    return await client.delete(
        f"{BOTS_URL}/{bot_id}/tokens/{token_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )


@pytest.mark.xfail(
    reason=(
        "uq_bot_token_name не partial — DB UNIQUE блокирует revoked-строки; "
        "exists_name фильтрует revoked_at IS NULL корректно, нужна миграция "
        "UNIQUE (bot_id, name) WHERE revoked_at IS NULL"
    ),
    strict=False,
)
async def test_bot_token_name_freed_after_revoke(client, admin_token, dept_a):
    """После revoke'а имя bot-токена можно использовать повторно — 201, не 409."""
    bot_id = await _make_bot(client, admin_token, dept_a.id, name="rn_name_test_bot")

    first = await _mint_token(client, admin_token, bot_id, "ci_deploy")
    assert first.status_code == 201, first.text
    token_id = first.json()["token_id"]

    revoke = await _revoke_token(client, admin_token, bot_id, token_id)
    assert revoke.status_code == 200, revoke.text

    second = await _mint_token(client, admin_token, bot_id, "ci_deploy")
    assert second.status_code == 201, (
        f"имя bot-токена должно освобождаться после revoke; got {second.text}"
    )
    # Новый токен отличается от первого
    assert second.json()["token"] != first.json()["token"]
    assert second.json()["token_id"] != token_id


@pytest.mark.xfail(
    reason=(
        "uq_bot_token_name не partial — цикл revoke→создать ломается на 2-м цикле "
        "из-за DB UNIQUE на (bot_id, name) без WHERE revoked_at IS NULL"
    ),
    strict=False,
)
async def test_multiple_revoked_tokens_same_name_allowed(client, admin_token, dept_a):
    """Можно revoke → создать → revoke → создать с одним именем несколько раз."""
    bot_id = await _make_bot(client, admin_token, dept_a.id, name="rn_multi_revoke_bot")
    tok_name = "rotation_slot"

    for cycle in range(3):
        mint = await _mint_token(client, admin_token, bot_id, tok_name)
        assert mint.status_code == 201, f"cycle {cycle}: {mint.text}"
        rev = await _revoke_token(client, admin_token, bot_id, mint.json()["token_id"])
        assert rev.status_code == 200, f"cycle {cycle} revoke: {rev.text}"


async def test_active_bot_token_name_conflict_still_409(client, admin_token, dept_a):
    """Пока первый токен активен — повторное имя → 409 TOKEN_NAME_ALREADY_EXISTS."""
    bot_id = await _make_bot(client, admin_token, dept_a.id, name="rn_conflict_bot")

    first = await _mint_token(client, admin_token, bot_id, "still_active")
    assert first.status_code == 201, first.text

    second = await _mint_token(client, admin_token, bot_id, "still_active")
    assert second.status_code == 409
    assert second.json()["error_code"] == "TOKEN_NAME_ALREADY_EXISTS"


async def test_bot_token_revoke_then_different_name_also_works(client, admin_token, dept_a):
    """После revoke старого токена можно создать новый с другим именем — sanity."""
    bot_id = await _make_bot(client, admin_token, dept_a.id, name="rn_diffname_bot")

    first = await _mint_token(client, admin_token, bot_id, "old_name")
    assert first.status_code == 201
    revoke = await _revoke_token(client, admin_token, bot_id, first.json()["token_id"])
    assert revoke.status_code == 200

    new_token = await _mint_token(client, admin_token, bot_id, "new_name")
    assert new_token.status_code == 201, new_token.text
