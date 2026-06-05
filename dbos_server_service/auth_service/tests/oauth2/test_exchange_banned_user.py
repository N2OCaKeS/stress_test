"""`POST /oauth2/token` — отбивает обмен code'а, если юзер забанен/заблокирован
между /authorize и /token.

До фикса `exchange_code` проверял только `user is None`. Окно:

  1. Юзер аппрувит /authorize → выдан code.
  2. Admin банит юзера.
  3. Клиент идёт в /token с тем же code'ом.
  4. Без фикса — выписывается JWT (introspect его сразу отобьёт по
     `is_banned=True`, но факт обмена + token уже у клиента).

Симметрично login-флоу, где banned/blocked отбиваются прямо в /login.
"""

from src.core.constants import UserStatus

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"


async def _make_client(http_client, admin_token, dept_id):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "banned_user_app",
            "department_id": dept_id,
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/callback"],
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_exchange_code_for_banned_user_rejected(
    client, admin_token, user_a, user_a_token, dept_a, db,
):
    cl = await _make_client(client, admin_token, dept_a.id)

    # /authorize — пока юзер активный.
    auth_resp = await client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        params={
            "client_id": cl["client_id"],
            "redirect_uri": "https://app.example.com/callback",
            "response_type": "code",
        },
        follow_redirects=False,
    )
    assert auth_resp.status_code == 302, auth_resp.text
    code = auth_resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]

    # Между /authorize и /token — банят юзера. Идём через service-call, чтобы
    # state (status + is_active + Ban-record) был полностью консистентным.
    from src.services import user_service
    await user_service.ban_user(
        db=db,
        actor_id="usr_admin_test",
        user_id=user_a.id,
        ban_type="permanent",
        reason="banned between authorize and token",
    )

    # Обмен code'а должен отбиться.
    token_resp = await client.post(
        TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": cl["client_id"],
            "client_secret": cl["client_secret"],
            "code": code,
            "redirect_uri": "https://app.example.com/callback",
        },
    )
    assert token_resp.status_code == 401, token_resp.text
    body = token_resp.json()
    assert body["error_code"] == "OAUTH_USER_INACTIVE"
    # Sanity: ни access_token, ни refresh_token не отдали.
    assert "access_token" not in body


async def test_exchange_code_for_blocked_user_rejected(
    client, admin_token, user_a, user_a_token, dept_a, db,
):
    """BLOCKED-юзер (мягче, чем ban — без Ban-record) тоже не должен обменять code."""
    cl = await _make_client(client, admin_token, dept_a.id)

    auth_resp = await client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        params={
            "client_id": cl["client_id"],
            "redirect_uri": "https://app.example.com/callback",
            "response_type": "code",
        },
        follow_redirects=False,
    )
    assert auth_resp.status_code == 302
    code = auth_resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]

    # Мутируем User напрямую — BLOCKED не имеет отдельного service-метода.
    user_a.status = UserStatus.BLOCKED
    user_a.is_active = False
    await db.flush()
    await db.commit()

    token_resp = await client.post(
        TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": cl["client_id"],
            "client_secret": cl["client_secret"],
            "code": code,
            "redirect_uri": "https://app.example.com/callback",
        },
    )
    assert token_resp.status_code == 401
    assert token_resp.json()["error_code"] == "OAUTH_USER_INACTIVE"


# ── /authorize отдельно: код вообще не должен выдаваться забаненному ─────────

async def test_authorize_for_banned_user_rejected_without_code(
    client, admin_token, user_a, user_a_token, dept_a, db,
):
    """До фикса /authorize возвращал 302 с code'ом даже для забаненного юзера —
    /token позже отбивал, но сам факт 200 vs ошибка — username-enumeration
    oracle. После фикса /authorize сразу 401 OAUTH_USER_INACTIVE."""
    cl = await _make_client(client, admin_token, dept_a.id)

    from src.services import user_service
    await user_service.ban_user(
        db=db,
        actor_id="usr_admin_test",
        user_id=user_a.id,
        ban_type="permanent",
        reason="banned before authorize",
    )

    auth_resp = await client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        params={
            "client_id": cl["client_id"],
            "redirect_uri": "https://app.example.com/callback",
            "response_type": "code",
        },
        follow_redirects=False,
    )
    # JWT юзера всё ещё валиден (бан мгновенно отбивается через identity-revalidate
    # в `get_current_identity`, либо в самом /authorize по статусу). Любая
    # ветка должна вернуть ошибку, не 302 с кодом.
    assert auth_resp.status_code in (401, 403), auth_resp.text
    assert "location" not in {h.lower() for h in auth_resp.headers.keys()} or \
        "code=" not in (auth_resp.headers.get("location") or "")
