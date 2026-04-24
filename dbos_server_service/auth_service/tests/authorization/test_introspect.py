"""Тесты: POST /api/auth/v1/authorization/introspect и /service-access — проверка токенов и доступа к сервисам."""

INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
ACCESS_URL = "/api/auth/v1/authorization/service-access"
LOGIN_URL = "/api/auth/v1/login"
TOKENS_URL = "/api/auth/v1/tokens"
BOTS_URL = "/api/auth/v1/bots"


# ── JWT introspect ────────────────────────────────────────────────────────────

async def test_valid_jwt_is_active(client, user_a_token):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["subject_type"] == "user"


async def test_invalid_token_is_inactive(client):
    resp = await client.post(INTROSPECT_URL, json={"token": "garbage.token.value"})
    assert resp.json()["active"] is False


async def test_jwt_contains_correct_sub(client, user_a, user_a_token):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert resp.json()["sub"] == user_a.id


async def test_jwt_contains_allowed_services(client, user_a_token, service_x):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert service_x.service_name in resp.json()["allowed_services"]


async def test_account_admin_jwt_has_no_services(client, admin_token):
    resp = await client.post(INTROSPECT_URL, json={"token": admin_token})
    assert resp.json()["allowed_services"] == []


# ── PAT introspect ────────────────────────────────────────────────────────────

async def test_pat_is_active(client, user_a_token):
    raw = (await client.post(TOKENS_URL, headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"name": "intr_pat", "allowed_services": []})).json()["token"]
    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.json()["active"] is True
    assert resp.json()["subject_type"] == "user"


# ── Bot token introspect ──────────────────────────────────────────────────────

async def test_bot_token_is_active(client, admin_token, dept_a):
    bot_id = (await client.post(BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                                 json={"name": "intr_bot2", "department_id": dept_a.id,
                                       "allowed_services": []})).json()["bot_id"]
    raw = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"name": "it"})).json()["token"]
    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.json()["active"] is True
    assert resp.json()["subject_type"] == "bot"


# ── Service access ────────────────────────────────────────────────────────────

async def test_service_access_allowed(client, user_a_token, service_x):
    resp = await client.post(ACCESS_URL, json={"subject_token": user_a_token, "service_name": service_x.service_name})
    assert resp.status_code == 200
    assert resp.json()["allowed"] is True


async def test_service_access_denied_wrong_service(client, user_a_token):
    resp = await client.post(ACCESS_URL, json={"subject_token": user_a_token, "service_name": "nonexistent_svc"})
    assert resp.json()["allowed"] is False


async def test_service_access_denied_invalid_token(client, service_x):
    resp = await client.post(ACCESS_URL, json={"subject_token": "bad_token", "service_name": service_x.service_name})
    assert resp.json()["allowed"] is False


async def test_service_access_dept_b_no_access_to_svc_x(client, user_b_token, service_x):
    """user_b is in dept_b which has no access to service_x."""
    resp = await client.post(ACCESS_URL, json={"subject_token": user_b_token, "service_name": service_x.service_name})
    assert resp.json()["allowed"] is False


async def test_service_access_includes_roles(client, user_a_token, service_x):
    resp = await client.post(ACCESS_URL, json={"subject_token": user_a_token, "service_name": service_x.service_name})
    assert "reader" in resp.json()["service_roles"]
