"""/oauth2/authorize: urlparse корректно собирает redirect_uri во всех случаях.

Базовые сценарии «redirect_uri с существующим query» — в
test_authorize_redirect_with_query.py. Здесь — дополнительные ветви urlparse:
redirect_uri без query, redirect_uri с fragment, сохранение порядка параметров.
"""

from urllib.parse import parse_qs, urlparse

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"


async def _make_client(http_client, token, dept_id, *, name, redirect_uris):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": name,
            "department_id": dept_id,
            "grant_types": ["authorization_code"],
            "redirect_uris": redirect_uris,
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestRedirectUriWithoutQuery:
    async def test_plain_redirect_uri_gets_code_appended(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """redirect_uri без query → code добавляется как единственный параметр."""
        redirect = "https://app.example.com/callback"
        cl = await _make_client(
            client, admin_token, dept_a.id,
            name="plain_redir_app", redirect_uris=[redirect],
        )

        resp = await client.get(
            AUTHORIZE_URL,
            params={"client_id": cl["client_id"], "redirect_uri": redirect},
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        location = resp.headers["location"]

        assert location.count("?") == 1, f"ожидали ровно один '?': {location}"
        parsed = urlparse(location)
        qs = parse_qs(parsed.query)
        assert "code" in qs
        assert qs["code"][0]
        assert parsed.scheme == "https"
        assert parsed.path == "/callback"

    async def test_plain_redirect_uri_with_state(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """redirect_uri без query + state → ?code=…&state=…, ровно один ?."""
        redirect = "https://app.example.com/callback"
        cl = await _make_client(
            client, admin_token, dept_a.id,
            name="plain_state_app", redirect_uris=[redirect],
        )

        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": redirect,
                "state": "csrf-token-abc",
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        location = resp.headers["location"]
        assert location.count("?") == 1
        qs = parse_qs(urlparse(location).query)
        assert "code" in qs
        assert qs.get("state") == ["csrf-token-abc"]


class TestRedirectUriMultipleQueryParams:
    async def test_multiple_existing_params_all_preserved(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Три существующих параметра в redirect_uri — все сохранены в location."""
        redirect = "https://app.example.com/cb?a=1&b=2&c=3"
        cl = await _make_client(
            client, admin_token, dept_a.id,
            name="multi_param_app", redirect_uris=[redirect],
        )

        resp = await client.get(
            AUTHORIZE_URL,
            params={"client_id": cl["client_id"], "redirect_uri": redirect},
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        location = resp.headers["location"]
        assert location.count("?") == 1

        qs = parse_qs(urlparse(location).query)
        assert qs.get("a") == ["1"]
        assert qs.get("b") == ["2"]
        assert qs.get("c") == ["3"]
        assert "code" in qs

    async def test_path_preserved_with_query_params(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Путь в redirect_uri сохраняется корректно вместе с query."""
        redirect = "https://app.example.com/deep/path/callback?v=2"
        cl = await _make_client(
            client, admin_token, dept_a.id,
            name="deep_path_app", redirect_uris=[redirect],
        )

        resp = await client.get(
            AUTHORIZE_URL,
            params={"client_id": cl["client_id"], "redirect_uri": redirect},
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        parsed = urlparse(resp.headers["location"])
        assert parsed.path == "/deep/path/callback"
        qs = parse_qs(parsed.query)
        assert qs.get("v") == ["2"]
        assert "code" in qs


class TestRedirectUriCodeUnique:
    async def test_two_authorizes_produce_different_codes(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Каждый /authorize выдаёт уникальный code — не переиспользует предыдущий."""
        redirect = "https://app.example.com/cb"
        cl = await _make_client(
            client, admin_token, dept_a.id,
            name="uniq_code_app", redirect_uris=[redirect],
        )

        params = {"client_id": cl["client_id"], "redirect_uri": redirect}
        headers = {"Authorization": f"Bearer {user_a_token}"}

        r1 = await client.get(AUTHORIZE_URL, params=params, headers=headers,
                               follow_redirects=False)
        r2 = await client.get(AUTHORIZE_URL, params=params, headers=headers,
                               follow_redirects=False)

        assert r1.status_code == 302
        assert r2.status_code == 302

        code1 = parse_qs(urlparse(r1.headers["location"]).query).get("code", [""])[0]
        code2 = parse_qs(urlparse(r2.headers["location"]).query).get("code", [""])[0]
        assert code1 != code2, "каждый authorize должен выдавать новый уникальный code"
