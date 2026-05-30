"""/oauth2/authorize → state кодируется как `%20`, а не `+`.

RFC 6749 §4.1.2 требует exact-echo `state`: клиент сравнивает байт-в-байт
со своим хранилищем для CSRF-защиты. `urlencode(..., quote_via=quote_plus)`
по дефолту превращает пробелы в `+`, что меняет байты и ломает сравнение
у клиентов, которые ждут `%20`-кодирование.
"""

from urllib.parse import urlparse

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"


async def _make_client(http_client, token, dept_id, *, redirect_uris):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "state_echo_app",
            "department_id": dept_id,
            "grant_types": ["authorization_code"],
            "redirect_uris": redirect_uris,
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_state_with_space_uses_percent20_not_plus(
    client, admin_token, user_a_token, dept_a,
):
    redirect = "https://app.example.com/cb"
    cl = await _make_client(client, admin_token, dept_a.id, redirect_uris=[redirect])

    state_with_space = "csrf token 123"
    resp = await client.get(
        AUTHORIZE_URL,
        params={
            "client_id": cl["client_id"],
            "redirect_uri": redirect,
            "state": state_with_space,
        },
        headers={"Authorization": f"Bearer {user_a_token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    query = urlparse(location).query
    # `+` означал бы `quote_plus`-режим — exact-echo сломан.
    assert "state=csrf+token+123" not in query, query
    assert "state=csrf%20token%20123" in query, query
