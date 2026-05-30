"""exchange_code: `mark_used` коммитится сразу, до проверок user'а.

RFC 6749 §4.1.2 — authorization code single-use инвариант. Без явного
commit'а после `mark_used` любой raise в pre-JWT-проверках (user_not_found
/ OAUTH_USER_INACTIVE) откатывает пометку через rollback в get_db(), и
атакующий может повторно обменять тот же код.

Сценарий: юзер аппрувит /authorize, между /authorize и /token его банят.
Первый /token — 401 OAUTH_USER_INACTIVE. Второй /token с тем же code
должен быть 401 INVALID_GRANT (used), а не 401 OAUTH_USER_INACTIVE.
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
            "name": "mark_used_persist_app",
            "department_id": dept_id,
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/callback"],
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_mark_used_persists_when_user_inactive(
    client, admin_token, user_a, user_a_token, dept_a, db,
):
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
    assert auth_resp.status_code == 302, auth_resp.text
    code = auth_resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]

    # Юзера блокируем — exchange упрётся в OAUTH_USER_INACTIVE.
    user_a.status = UserStatus.BLOCKED
    user_a.is_active = False
    await db.flush()
    await db.commit()

    payload = {
        "grant_type": "authorization_code",
        "client_id": cl["client_id"],
        "client_secret": cl["client_secret"],
        "code": code,
        "redirect_uri": "https://app.example.com/callback",
    }

    first = await client.post(TOKEN_URL, json=payload)
    assert first.status_code == 401, first.text
    assert first.json()["error_code"] == "OAUTH_USER_INACTIVE"

    # Повторный обмен того же кода: даже если бы юзера сейчас разблокировали,
    # код уже потрачен — должен ответить INVALID_GRANT, а не пропустить
    # обмен заново.
    second = await client.post(TOKEN_URL, json=payload)
    assert second.status_code == 401, second.text
    assert second.json()["error_code"] == "INVALID_GRANT", (
        "после первого fail'а на inactive user mark_used должен быть "
        f"закоммичен; повтор обмена ожидался INVALID_GRANT, got: {second.json()}"
    )
