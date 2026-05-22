"""Тесты: cross-tenant scope для bot-token endpoints.

До фикса `create_bot_token` / `list_bot_tokens` / `revoke_bot_token` не делали
`_require_can_manage_bot` (асимметрия с `assign_bot_roles` / `revoke_bot_roles`).
dept_admin одного отдела мог выписать / посмотреть / отозвать токен бота другого
отдела.

Сценарии:
- account_admin может работать с токенами любого бота — 200/201.
- dept_admin может работать с токенами только своего dept'а — 200/201.
- dept_admin → cross-dept бот → 403 BOT_ROLE_MGMT_FORBIDDEN.
"""

BOTS_URL = "/api/auth/v1/bots"


async def _create_bot(client, token, dept_id, name="scope_bot", services=None):
    resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": services or []},
    )
    return resp.json()


async def _issue_token(client, token, bot_id, name="scope_tok"):
    resp = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    return resp


# ── create_bot_token ──────────────────────────────────────────────────────────

async def test_account_admin_creates_token_for_any_bot(
    client, admin_token, dept_a, dept_b,
):
    """account_admin может выпустить токен любому боту (любой dept)."""
    bot_a = await _create_bot(client, admin_token, dept_a.id, name="acct_a_bot")
    bot_b = await _create_bot(client, admin_token, dept_b.id, name="acct_b_bot")

    resp_a = await _issue_token(client, admin_token, bot_a["bot_id"], name="t_a")
    resp_b = await _issue_token(client, admin_token, bot_b["bot_id"], name="t_b")

    assert resp_a.status_code == 201
    assert resp_b.status_code == 201


async def test_dept_admin_creates_token_for_own_dept_bot(
    client, admin_token, dept_admin_a_token, dept_a,
):
    """dept_admin своего dept'а — может выпустить токен."""
    bot = await _create_bot(client, admin_token, dept_a.id, name="own_dept_bot")
    resp = await _issue_token(client, dept_admin_a_token, bot["bot_id"], name="own_tok")
    assert resp.status_code == 201


async def test_dept_admin_cannot_create_token_for_cross_dept_bot(
    client, admin_token, dept_admin_a_token, dept_b,
):
    """dept_admin dept_a → бот dept_b → 403 (cross-tenant token minting запрещён)."""
    bot = await _create_bot(client, admin_token, dept_b.id, name="cross_mint_bot")
    resp = await _issue_token(client, dept_admin_a_token, bot["bot_id"], name="stolen")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BOT_ROLE_MGMT_FORBIDDEN"


async def test_create_token_for_nonexistent_bot_returns_404(
    client, dept_admin_a_token,
):
    """Несуществующий bot → 404 BOT_NOT_FOUND (проверка раньше scope-guard)."""
    resp = await _issue_token(client, dept_admin_a_token, "bot_does_not_exist", name="ghost")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "BOT_NOT_FOUND"


# ── list_bot_tokens ───────────────────────────────────────────────────────────

async def test_account_admin_lists_tokens_for_any_bot(
    client, admin_token, dept_a, dept_b,
):
    bot_a = await _create_bot(client, admin_token, dept_a.id, name="ls_a_bot")
    bot_b = await _create_bot(client, admin_token, dept_b.id, name="ls_b_bot")
    await _issue_token(client, admin_token, bot_a["bot_id"], name="ls_t_a")
    await _issue_token(client, admin_token, bot_b["bot_id"], name="ls_t_b")

    resp_a = await client.get(
        f"{BOTS_URL}/{bot_a['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    resp_b = await client.get(
        f"{BOTS_URL}/{bot_b['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_a.status_code == 200 and len(resp_a.json()) == 1
    assert resp_b.status_code == 200 and len(resp_b.json()) == 1


async def test_dept_admin_lists_tokens_for_own_dept_bot(
    client, admin_token, dept_admin_a_token, dept_a,
):
    bot = await _create_bot(client, admin_token, dept_a.id, name="ls_own_bot")
    await _issue_token(client, admin_token, bot["bot_id"], name="ls_own_tok")

    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_dept_admin_cannot_list_tokens_for_cross_dept_bot(
    client, admin_token, dept_admin_a_token, dept_b,
):
    """dept_admin dept_a → список токенов бота dept_b → 403 (metadata leak запрещён)."""
    bot = await _create_bot(client, admin_token, dept_b.id, name="ls_cross_bot")
    await _issue_token(client, admin_token, bot["bot_id"], name="ls_cross_tok")

    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BOT_ROLE_MGMT_FORBIDDEN"


# ── revoke_bot_token ──────────────────────────────────────────────────────────

async def test_account_admin_revokes_token_for_any_bot(
    client, admin_token, dept_b,
):
    bot = await _create_bot(client, admin_token, dept_b.id, name="rv_acct_bot")
    tok_id = (await _issue_token(client, admin_token, bot["bot_id"], name="rv_acct_tok")).json()["token_id"]

    resp = await client.delete(
        f"{BOTS_URL}/{bot['bot_id']}/tokens/{tok_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


async def test_dept_admin_revokes_token_for_own_dept_bot(
    client, admin_token, dept_admin_a_token, dept_a,
):
    bot = await _create_bot(client, admin_token, dept_a.id, name="rv_own_bot")
    tok_id = (await _issue_token(client, admin_token, bot["bot_id"], name="rv_own_tok")).json()["token_id"]

    resp = await client.delete(
        f"{BOTS_URL}/{bot['bot_id']}/tokens/{tok_id}",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 200


async def test_dept_admin_cannot_revoke_token_for_cross_dept_bot(
    client, admin_token, dept_admin_a_token, dept_b,
):
    """dept_admin dept_a → отзыв токена бота dept_b → 403 (DoS других dept'ов запрещён)."""
    bot = await _create_bot(client, admin_token, dept_b.id, name="rv_cross_bot")
    tok_id = (await _issue_token(client, admin_token, bot["bot_id"], name="rv_cross_tok")).json()["token_id"]

    resp = await client.delete(
        f"{BOTS_URL}/{bot['bot_id']}/tokens/{tok_id}",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BOT_ROLE_MGMT_FORBIDDEN"


# ── Regular user still locked out (sanity: AnyAdmin guard intact) ─────────────

async def test_regular_user_cannot_create_bot_token(
    client, admin_token, user_a_token, dept_a,
):
    bot = await _create_bot(client, admin_token, dept_a.id, name="user_locked_bot")
    resp = await _issue_token(client, user_a_token, bot["bot_id"], name="user_tok")
    assert resp.status_code == 403


async def test_regular_user_cannot_list_bot_tokens(
    client, admin_token, user_a_token, dept_a,
):
    bot = await _create_bot(client, admin_token, dept_a.id, name="user_ls_bot")
    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert resp.status_code == 403
