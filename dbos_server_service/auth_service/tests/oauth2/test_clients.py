"""Тесты: /api/auth/v1/oauth2/clients — OAuth2-клиенты и потоки получения токенов."""

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"


async def _create_client(client, token, dept_id, name="test_app", grant_types=None,
                   redirect_uris=None, scopes=None):
    return await client.post(CLIENTS_URL,
                       headers={"Authorization": f"Bearer {token}"},
                       json={
                           "department_id": dept_id,
                           "name": name,
                           "grant_types": grant_types or ["authorization_code"],
                           "redirect_uris": redirect_uris or ["https://app.example.com/callback"],
                           "allowed_scopes": scopes or [],
                       })


# ── Create client ─────────────────────────────────────────────────────────────

async def test_admin_creates_oauth_client(client, admin_token, dept_a):
    resp = await _create_client(client, admin_token, dept_a.id)
    assert resp.status_code == 201
    body = resp.json()
    assert "client_id" in body
    assert "client_secret" in body
    assert body["client_secret"].startswith("cs_")
    assert body["department_id"] == dept_a.id


async def test_dept_admin_creates_client_in_own_dept(client, dept_admin_a_token, dept_a):
    resp = await _create_client(client, dept_admin_a_token, dept_a.id, name="own_app")
    assert resp.status_code == 201


async def test_dept_admin_cannot_create_client_in_other_dept(client, dept_admin_a_token, dept_b):
    resp = await _create_client(client, dept_admin_a_token, dept_b.id, name="cross_app")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


async def test_regular_user_cannot_create_client(client, user_a_token, dept_a):
    resp = await _create_client(client, user_a_token, dept_a.id, name="hacker_app")
    assert resp.status_code == 403


async def test_duplicate_client_name_returns_409(client, admin_token, dept_a):
    await _create_client(client, admin_token, dept_a.id, name="dup_app")
    resp = await _create_client(client, admin_token, dept_a.id, name="dup_app")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "OAUTH_CLIENT_NAME_EXISTS"


async def test_client_secret_not_exposed_in_list(client, admin_token, dept_a):
    await _create_client(client, admin_token, dept_a.id, name="list_test_app")
    resp = await client.get(CLIENTS_URL,
                      headers={"Authorization": f"Bearer {admin_token}"},
                      params={"department_id": dept_a.id})
    assert resp.status_code == 200
    for c in resp.json():
        assert "client_secret" not in c


async def test_nonexistent_dept_returns_404(client, admin_token):
    resp = await _create_client(client, admin_token, "dep_doesnotexist", name="ghost_app")
    assert resp.status_code == 404


# ── List clients ──────────────────────────────────────────────────────────────

async def test_admin_lists_clients_by_department(client, admin_token, dept_a, dept_b):
    await _create_client(client, admin_token, dept_a.id, name="app_a1")
    await _create_client(client, admin_token, dept_b.id, name="app_b1")
    resp = await client.get(CLIENTS_URL,
                      headers={"Authorization": f"Bearer {admin_token}"},
                      params={"department_id": dept_a.id})
    names = [c["name"] for c in resp.json()]
    assert "app_a1" in names
    assert "app_b1" not in names


async def test_dept_admin_only_sees_own_dept_clients(client, dept_admin_a_token, admin_token, dept_a, dept_b):
    await _create_client(client, admin_token, dept_a.id, name="visible_app")
    await _create_client(client, admin_token, dept_b.id, name="hidden_app")
    resp = await client.get(CLIENTS_URL,
                      headers={"Authorization": f"Bearer {dept_admin_a_token}"})
    names = [c["name"] for c in resp.json()]
    assert "visible_app" in names
    assert "hidden_app" not in names


# ── Delete client ─────────────────────────────────────────────────────────────

async def test_admin_deletes_client(client, admin_token, dept_a):
    client_id = (await _create_client(client, admin_token, dept_a.id, name="to_delete")).json()["id"]
    resp = await client.delete(f"{CLIENTS_URL}/{client_id}",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


async def test_dept_admin_deletes_own_dept_client(client, dept_admin_a_token, admin_token, dept_a):
    client_id = (await _create_client(client, admin_token, dept_a.id, name="del_own")).json()["id"]
    resp = await client.delete(f"{CLIENTS_URL}/{client_id}",
                         headers={"Authorization": f"Bearer {dept_admin_a_token}"})
    assert resp.status_code == 200


async def test_dept_admin_cannot_delete_other_dept_client(client, dept_admin_a_token, admin_token, dept_b):
    client_id = (await _create_client(client, admin_token, dept_b.id, name="other_dept_app")).json()["id"]
    resp = await client.delete(f"{CLIENTS_URL}/{client_id}",
                         headers={"Authorization": f"Bearer {dept_admin_a_token}"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


async def test_delete_nonexistent_client_returns_404(client, admin_token):
    resp = await client.delete(f"{CLIENTS_URL}/cli_doesnotexist",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 404


# ── client_credentials grant ──────────────────────────────────────────────────

async def test_client_credentials_issues_token(client, admin_token, dept_a):
    created = (await _create_client(client, admin_token, dept_a.id, name="m2m_app",
                             grant_types=["client_credentials"])).json()
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": created["client_id"],
        "client_secret": created["client_secret"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "Bearer"


async def test_client_credentials_wrong_secret_returns_401(client, admin_token, dept_a):
    created = (await _create_client(client, admin_token, dept_a.id, name="m2m_bad_app",
                             grant_types=["client_credentials"])).json()
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": created["client_id"],
        "client_secret": "wrong_secret",
    })
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "OAUTH_CLIENT_INVALID"


async def test_client_credentials_grant_not_allowed_returns_403(client, admin_token, dept_a):
    """Client only has authorization_code — client_credentials must be rejected."""
    created = (await _create_client(client, admin_token, dept_a.id, name="code_only_app",
                             grant_types=["authorization_code"])).json()
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": created["client_id"],
        "client_secret": created["client_secret"],
    })
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "GRANT_TYPE_NOT_ALLOWED"


async def test_client_credentials_deactivated_client_returns_401(client, admin_token, dept_a):
    created = (await _create_client(client, admin_token, dept_a.id, name="deact_app",
                             grant_types=["client_credentials"])).json()
    await client.delete(f"{CLIENTS_URL}/{created['id']}",
                  headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": created["client_id"],
        "client_secret": created["client_secret"],
    })
    assert resp.status_code == 401


# ── Authorization code flow ───────────────────────────────────────────────────

async def test_authorize_issues_code_and_redirects(client, admin_token, user_a_token, dept_a):
    created = (await _create_client(client, admin_token, dept_a.id, name="code_app",
                             grant_types=["authorization_code"],
                             redirect_uris=["https://app.example.com/callback"])).json()
    resp = await client.get(AUTHORIZE_URL,
                      headers={"Authorization": f"Bearer {user_a_token}"},
                      params={
                          "client_id": created["client_id"],
                          "redirect_uri": "https://app.example.com/callback",
                          "response_type": "code",
                          "state": "xyz123",
                      },
                      follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "code=" in location
    assert "state=xyz123" in location


async def test_authorize_unknown_client_returns_401(client, user_a_token):
    resp = await client.get(AUTHORIZE_URL,
                      headers={"Authorization": f"Bearer {user_a_token}"},
                      params={
                          "client_id": "unknown_client_id",
                          "redirect_uri": "https://app.example.com/callback",
                          "response_type": "code",
                      },
                      follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "OAUTH_CLIENT_INVALID"


async def test_authorize_redirect_uri_mismatch_returns_403(client, admin_token, user_a_token, dept_a):
    created = (await _create_client(client, admin_token, dept_a.id, name="uri_check_app",
                             grant_types=["authorization_code"],
                             redirect_uris=["https://app.example.com/callback"])).json()
    resp = await client.get(AUTHORIZE_URL,
                      headers={"Authorization": f"Bearer {user_a_token}"},
                      params={
                          "client_id": created["client_id"],
                          "redirect_uri": "https://evil.example.com/steal",
                          "response_type": "code",
                      },
                      follow_redirects=False)
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "REDIRECT_URI_MISMATCH"


async def test_authorization_code_exchange(client, admin_token, user_a_token, dept_a):
    created = (await _create_client(client, admin_token, dept_a.id, name="exchange_app",
                             grant_types=["authorization_code"],
                             redirect_uris=["https://app.example.com/callback"])).json()
    auth_resp = await client.get(AUTHORIZE_URL,
                           headers={"Authorization": f"Bearer {user_a_token}"},
                           params={
                               "client_id": created["client_id"],
                               "redirect_uri": "https://app.example.com/callback",
                               "response_type": "code",
                           },
                           follow_redirects=False)
    location = auth_resp.headers["location"]
    code = location.split("code=")[1].split("&")[0]

    token_resp = await client.post(TOKEN_URL, json={
        "grant_type": "authorization_code",
        "client_id": created["client_id"],
        "client_secret": created["client_secret"],
        "code": code,
        "redirect_uri": "https://app.example.com/callback",
    })
    assert token_resp.status_code == 200
    body = token_resp.json()
    assert "access_token" in body
    assert body["token_type"] == "Bearer"


async def test_authorization_code_cannot_be_reused(client, admin_token, user_a_token, dept_a):
    """Authorization codes are single-use."""
    created = (await _create_client(client, admin_token, dept_a.id, name="single_use_app",
                             grant_types=["authorization_code"],
                             redirect_uris=["https://app.example.com/callback"])).json()
    auth_resp = await client.get(AUTHORIZE_URL,
                           headers={"Authorization": f"Bearer {user_a_token}"},
                           params={
                               "client_id": created["client_id"],
                               "redirect_uri": "https://app.example.com/callback",
                               "response_type": "code",
                           },
                           follow_redirects=False)
    code = auth_resp.headers["location"].split("code=")[1].split("&")[0]
    exchange_payload = {
        "grant_type": "authorization_code",
        "client_id": created["client_id"],
        "client_secret": created["client_secret"],
        "code": code,
        "redirect_uri": "https://app.example.com/callback",
    }
    await client.post(TOKEN_URL, json=exchange_payload)
    resp2 = await client.post(TOKEN_URL, json=exchange_payload)
    assert resp2.status_code == 401
    assert resp2.json()["error_code"] == "OAUTH_CODE_INVALID"


async def test_unsupported_grant_type_returns_error(client):
    """`grant_type='implicit'` не в Literal — Pydantic v2 отбивает на схеме (422).

    Раньше схема пропускала любую строку, и `UNSUPPORTED_GRANT_TYPE` поднимал
    endpoint. Сейчас Literal-валидация ловит bad-grant до handler'а.
    """
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "implicit",
        "client_id": "any",
        "client_secret": "any",
    })
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "VALIDATION_ERROR"


# ── Authorize: дополнительные пути ────────────────────────────────────────────

async def test_authorize_without_jwt_returns_401(client, admin_token, dept_a):
    """/authorize требует CurrentIdentity — без Bearer JWT → 401."""
    created = (await _create_client(client, admin_token, dept_a.id, name="noauth_app",
                                     grant_types=["authorization_code"])).json()
    resp = await client.get(AUTHORIZE_URL,
                            params={
                                "client_id": created["client_id"],
                                "redirect_uri": "https://app.example.com/callback",
                                "response_type": "code",
                            },
                            follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_TOKEN"


async def test_authorize_with_deleted_client_returns_401(client, admin_token, user_a_token, dept_a):
    """После удаления клиента /authorize не должен выдавать код."""
    created = (await _create_client(client, admin_token, dept_a.id, name="zombie_app",
                                     grant_types=["authorization_code"])).json()
    await client.delete(f"{CLIENTS_URL}/{created['id']}",
                        headers={"Authorization": f"Bearer {admin_token}"})

    resp = await client.get(AUTHORIZE_URL,
                            headers={"Authorization": f"Bearer {user_a_token}"},
                            params={
                                "client_id": created["client_id"],
                                "redirect_uri": "https://app.example.com/callback",
                                "response_type": "code",
                            },
                            follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "OAUTH_CLIENT_INVALID"


# ── Exchange code: проверки на этапе обмена ───────────────────────────────────


async def _issue_code(client, admin_token, user_a_token, dept_id, name, redirect_uri="https://app.example.com/callback"):
    created = (await _create_client(client, admin_token, dept_id, name=name,
                                     grant_types=["authorization_code"],
                                     redirect_uris=[redirect_uri])).json()
    auth_resp = await client.get(AUTHORIZE_URL,
                                  headers={"Authorization": f"Bearer {user_a_token}"},
                                  params={
                                      "client_id": created["client_id"],
                                      "redirect_uri": redirect_uri,
                                      "response_type": "code",
                                  },
                                  follow_redirects=False)
    code = auth_resp.headers["location"].split("code=")[1].split("&")[0]
    return created, code


async def test_exchange_wrong_client_secret_returns_401(client, admin_token, user_a_token, dept_a):
    created, code = await _issue_code(client, admin_token, user_a_token, dept_a.id, "wrong_secret_app")
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "authorization_code",
        "client_id": created["client_id"],
        "client_secret": "cs_wrong_secret_value",
        "code": code,
        "redirect_uri": "https://app.example.com/callback",
    })
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "OAUTH_CLIENT_INVALID"


async def test_exchange_unknown_client_id_returns_401(client, admin_token, user_a_token, dept_a):
    _, code = await _issue_code(client, admin_token, user_a_token, dept_a.id, "unknown_cid_app")
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "authorization_code",
        "client_id": "completely_unknown_client_id",
        "client_secret": "cs_anything",
        "code": code,
        "redirect_uri": "https://app.example.com/callback",
    })
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "OAUTH_CLIENT_INVALID"


async def test_exchange_redirect_uri_mismatch_returns_403(client, admin_token, user_a_token, dept_a):
    """Код выдан под redirect_uri=callback, обмен с другим uri → 403 REDIRECT_URI_MISMATCH."""
    created, code = await _issue_code(client, admin_token, user_a_token, dept_a.id, "uri_mismatch_app")
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "authorization_code",
        "client_id": created["client_id"],
        "client_secret": created["client_secret"],
        "code": code,
        "redirect_uri": "https://different.example.com/callback",
    })
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "REDIRECT_URI_MISMATCH"


async def test_exchange_with_unknown_code_returns_401(client, admin_token, dept_a):
    created = (await _create_client(client, admin_token, dept_a.id, name="bad_code_app",
                                     grant_types=["authorization_code"])).json()
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "authorization_code",
        "client_id": created["client_id"],
        "client_secret": created["client_secret"],
        "code": "code_never_issued",
        "redirect_uri": "https://app.example.com/callback",
    })
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "OAUTH_CODE_INVALID"
