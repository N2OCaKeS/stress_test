"""Тесты: /api/auth/v1/tokens — личные токены доступа (PAT)."""

URL = "/api/auth/v1/tokens"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def _create_pat(client, token, name="my_pat", scopes=None):
    body = {"name": name, "allowed_services": scopes or []}
    return await client.post(URL, headers={"Authorization": f"Bearer {token}"}, json=body)


# ── Create ────────────────────────────────────────────────────────────────────

async def test_user_creates_pat(client, user_a_token):
    resp = await _create_pat(client, user_a_token)
    assert resp.status_code == 201
    body = resp.json()
    assert "token" in body
    assert body["token"].startswith("dbos_pat_")


async def test_pat_shown_only_once_in_response(client, user_a_token):
    resp = await _create_pat(client, user_a_token, name="once_pat")
    token_val = resp.json()["token"]
    list_resp = await client.get(URL, headers={"Authorization": f"Bearer {user_a_token}"})
    tokens_list = list_resp.json()
    assert not any(t.get("token") == token_val for t in tokens_list)


async def test_duplicate_pat_name_returns_409(client, user_a_token):
    await _create_pat(client, user_a_token, name="dup_pat")
    resp = await _create_pat(client, user_a_token, name="dup_pat")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "TOKEN_NAME_ALREADY_EXISTS"


async def test_admin_creates_pat(client, admin_token):
    resp = await _create_pat(client, admin_token, name="admin_pat")
    assert resp.status_code == 201


# ── List ──────────────────────────────────────────────────────────────────────

async def test_list_pats_no_secrets(client, user_a_token):
    await _create_pat(client, user_a_token, name="listed_pat")
    resp = await client.get(URL, headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200
    for t in resp.json():
        assert "token" not in t
        assert "token_hash" not in t
        assert "token_prefix" in t


async def test_user_sees_only_own_pats(client, user_a_token, user_b_token):
    await _create_pat(client, user_a_token, name="pat_a")
    await _create_pat(client, user_b_token, name="pat_b")
    resp_a = await client.get(URL, headers={"Authorization": f"Bearer {user_a_token}"})
    names_a = [t["name"] for t in resp_a.json()]
    assert "pat_a" in names_a
    assert "pat_b" not in names_a


# ── Revoke ────────────────────────────────────────────────────────────────────

async def test_revoke_pat(client, user_a_token):
    pat_id = (await _create_pat(client, user_a_token, name="to_revoke")).json()["token_id"]
    resp = await client.delete(f"{URL}/{pat_id}", headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200


async def test_double_revoke_returns_409(client, user_a_token):
    pat_id = (await _create_pat(client, user_a_token, name="double_revoke")).json()["token_id"]
    await client.delete(f"{URL}/{pat_id}", headers={"Authorization": f"Bearer {user_a_token}"})
    resp = await client.delete(f"{URL}/{pat_id}", headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "TOKEN_ALREADY_REVOKED"


async def test_cannot_revoke_other_users_pat(client, user_a_token, user_b_token):
    pat_id = (await _create_pat(client, user_b_token, name="b_pat_for_a")).json()["token_id"]
    resp = await client.delete(f"{URL}/{pat_id}", headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 404


# ── Introspect with PAT ───────────────────────────────────────────────────────

async def test_pat_works_in_introspect(client, user_a_token):
    raw_token = (await _create_pat(client, user_a_token, name="introspect_pat")).json()["token"]
    resp = await client.post(INTROSPECT_URL, json={"token": raw_token})
    assert resp.status_code == 200
    assert resp.json()["active"] is True


async def test_revoked_pat_inactive_in_introspect(client, user_a_token):
    data = (await _create_pat(client, user_a_token, name="rev_introspect")).json()
    await client.delete(f"{URL}/{data['token_id']}", headers={"Authorization": f"Bearer {user_a_token}"})
    resp = await client.post(INTROSPECT_URL, json={"token": data["token"]})
    assert resp.json()["active"] is False
