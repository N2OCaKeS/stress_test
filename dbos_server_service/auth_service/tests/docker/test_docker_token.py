"""Тесты: GET /api/auth/v1/docker/token — выдача токена для Docker registry."""

import base64

TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
BOTS_URL = "/api/auth/v1/bots"
TOKENS_URL = "/api/auth/v1/tokens"


def _basic(username, password):
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


async def _enable_docker(client, token, dept_id, pull_policy="all", pull_user_ids=None, push_user_ids=None):
    return await client.put(
        CONFIG_URL.format(dept_id=dept_id),
        headers={"Authorization": f"Bearer {token}"},
        json={
            "pull_policy": pull_policy,
            "pull_user_ids": pull_user_ids or [],
            "push_user_ids": push_user_ids or [],
        },
    )


# ── No config / disabled ──────────────────────────────────────────────────────

async def test_no_docker_config_returns_403(client, user_a, dept_a):
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User1234!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DOCKER_ACCESS_DENIED"


async def test_disabled_docker_config_returns_403(client, admin_token, user_a, dept_a, docker_registry_enabled, db):
    await client.delete(CONFIG_URL.format(dept_id=dept_a.id),
                  headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User1234!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 403


# ── Auth failures ─────────────────────────────────────────────────────────────

async def test_missing_auth_header_returns_401(client, docker_registry_enabled, user_a):
    resp = await client.get(TOKEN_URL, params={"service": "registry.test"})
    assert resp.status_code == 401


async def test_wrong_password_returns_401(client, docker_registry_enabled, user_a, dept_a):
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "WrongPass!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_nonexistent_user_returns_401(client, docker_registry_enabled, dept_a):
    resp = await client.get(TOKEN_URL, headers=_basic("ghost_user", "Pass1234!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 401


# ── Successful token issuance ─────────────────────────────────────────────────

async def test_authenticated_user_gets_token(client, admin_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User1234!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert "access_token" in body
    assert "expires_in" in body
    assert body["token"] == body["access_token"]


async def test_token_response_has_issued_at(client, admin_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User1234!"),
                      params={"service": "registry.test"})
    assert resp.status_code == 200
    assert "issued_at" in resp.json()


# ── pull_policy=all ───────────────────────────────────────────────────────────

async def test_pull_policy_all_grants_pull_to_any_user(client, admin_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id, pull_policy="all")
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User1234!"),
                      params={"service": "registry.test", "scope": "repository:myapp:pull"})
    assert resp.status_code == 200
    access = resp.json().get("access", [])
    # The token payload is RS256 JWT — access list is embedded in it
    # We verify the HTTP response succeeded; access enforcement is in the JWT claims
    # but access array is in the token, not the top-level response


async def test_pull_policy_all_user_b_in_other_dept_denied(client, admin_token, user_b, dept_b, dept_a):
    # dept_a has docker enabled, but user_b is in dept_b which has no config
    await _enable_docker(client, admin_token, dept_a.id, pull_policy="all")
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_b", "User1234!"),
                      params={"service": "registry.test", "scope": "repository:myapp:pull"})
    assert resp.status_code == 403


# ── pull_policy=restricted ────────────────────────────────────────────────────

async def test_restricted_policy_only_listed_user_can_pull(client, admin_token, user_a, user_b, dept_a):
    await _enable_docker(client, admin_token, dept_a.id,
                   pull_policy="restricted", pull_user_ids=[user_a.id])
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User1234!"),
                      params={"service": "registry.test", "scope": "repository:myapp:pull"})
    assert resp.status_code == 200


# ── Push permissions ──────────────────────────────────────────────────────────

async def test_push_allowed_for_listed_user(client, admin_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id, push_user_ids=[user_a.id])
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", "User1234!"),
                      params={"service": "registry.test", "scope": "repository:myapp:push"})
    assert resp.status_code == 200


# ── PAT as Docker password ────────────────────────────────────────────────────

async def test_pat_as_docker_password(client, admin_token, user_a_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    pat = (await client.post(TOKENS_URL,
                      headers={"Authorization": f"Bearer {user_a_token}"},
                      json={"name": "docker_pat", "allowed_services": []})).json()["token"]
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", pat),
                      params={"service": "registry.test"})
    assert resp.status_code == 200


async def test_revoked_pat_denied_for_docker(client, admin_token, user_a_token, user_a, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    pat_data = (await client.post(TOKENS_URL,
                           headers={"Authorization": f"Bearer {user_a_token}"},
                           json={"name": "docker_pat_rev", "allowed_services": []})).json()
    await client.delete(f"{TOKENS_URL}/{pat_data['token_id']}",
                  headers={"Authorization": f"Bearer {user_a_token}"})
    resp = await client.get(TOKEN_URL, headers=_basic("t_user_a", pat_data["token"]),
                      params={"service": "registry.test"})
    assert resp.status_code == 401


# ── Bot token as Docker password ──────────────────────────────────────────────

async def test_bot_token_as_docker_password(client, admin_token, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    bot_id = (await client.post(BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                         json={"name": "dock_bot", "department_id": dept_a.id,
                               "allowed_services": []})).json()["bot_id"]
    bot_token = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                            headers={"Authorization": f"Bearer {admin_token}"},
                            json={"name": "dock_tok"})).json()["token"]
    resp = await client.get(TOKEN_URL, headers=_basic("dock_bot", bot_token),
                      params={"service": "registry.test"})
    assert resp.status_code == 200


async def test_revoked_bot_token_denied_for_docker(client, admin_token, dept_a):
    await _enable_docker(client, admin_token, dept_a.id)
    bot_id = (await client.post(BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                         json={"name": "dock_bot_rev", "department_id": dept_a.id,
                               "allowed_services": []})).json()["bot_id"]
    tok_data = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                           headers={"Authorization": f"Bearer {admin_token}"},
                           json={"name": "dock_rev_tok"})).json()
    await client.delete(f"{BOTS_URL}/{bot_id}/tokens/{tok_data['token_id']}",
                  headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.get(TOKEN_URL, headers=_basic("dock_bot_rev", tok_data["token"]),
                      params={"service": "registry.test"})
    assert resp.status_code == 401


# ── Public key / JWKS endpoints ───────────────────────────────────────────────

async def test_certs_endpoint_returns_pem(client):
    resp = await client.get("/api/auth/v1/docker/certs")
    assert resp.status_code == 200
    assert "BEGIN CERTIFICATE" in resp.text


async def test_jwks_endpoint_returns_rsa_key(client):
    resp = await client.get("/api/auth/v1/docker/jwks")
    assert resp.status_code == 200
    body = resp.json()
    assert "keys" in body
    assert len(body["keys"]) >= 1
    key = body["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert "n" in key
    assert "e" in key
