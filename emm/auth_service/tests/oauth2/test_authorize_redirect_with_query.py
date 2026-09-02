"""/oauth2/authorize корректно достраивает query, если redirect_uri уже содержит `?`.

Раньше `f"{redirect_uri}?code=…"` давал двойной `?` (`…?env=prod?code=…`),
ломая парсинг location на клиенте. Теперь дописываем code/state через
urlparse + urlencode + urlunparse.
"""

from urllib.parse import parse_qs, urlparse

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"


async def _make_client(http_client, token, dept_id, *, redirect_uris):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "redir_q_app",
            "department_id": dept_id,
            "grant_types": ["authorization_code"],
            "redirect_uris": redirect_uris,
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_redirect_uri_with_existing_query_appends_code(
    client, admin_token, user_a_token, dept_a,
):
    redirect = "https://app.example.com/cb?env=prod&tenant=acme"
    cl = await _make_client(client, admin_token, dept_a.id, redirect_uris=[redirect])

    resp = await client.get(
        AUTHORIZE_URL,
        params={
            "client_id": cl["client_id"],
            "redirect_uri": redirect,
        },
        headers={"Authorization": f"Bearer {user_a_token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]

    # Ровно один `?` — иначе клиент не распарсит query.
    assert location.count("?") == 1, f"malformed query separators: {location}"

    parsed = urlparse(location)
    qs = parse_qs(parsed.query)
    assert qs.get("env") == ["prod"]
    assert qs.get("tenant") == ["acme"]
    assert "code" in qs and qs["code"][0]
    assert parsed.scheme == "https"
    assert parsed.netloc == "app.example.com"
    assert parsed.path == "/cb"


async def test_redirect_uri_with_query_carries_state(
    client, admin_token, user_a_token, dept_a,
):
    redirect = "https://app.example.com/cb?env=prod"
    cl = await _make_client(client, admin_token, dept_a.id, redirect_uris=[redirect])

    resp = await client.get(
        AUTHORIZE_URL,
        params={
            "client_id": cl["client_id"],
            "redirect_uri": redirect,
            "state": "xyz-csrf-123",
        },
        headers={"Authorization": f"Bearer {user_a_token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.count("?") == 1
    qs = parse_qs(urlparse(location).query)
    assert qs.get("env") == ["prod"]
    assert qs.get("state") == ["xyz-csrf-123"]
    assert "code" in qs
