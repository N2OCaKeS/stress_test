"""Tests: public OAuth-клиенты обязаны идти PKCE S256.

Закрывает SEC-auth P1-3: `is_public=True` принудительно требует
`code_challenge` + `code_challenge_method=S256`. Confidential
(`is_public=False`, default) — поведение без изменений (back-compat).
"""

import base64
import hashlib

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def _make_client(client, admin_token, dept_id, *, name, is_public,
                       redirect_uri="https://app.example.com/cb"):
    resp = await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["authorization_code"],
            "redirect_uris": [redirect_uri],
            "allowed_scopes": [],
            "is_public": is_public,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _authorize(client, user_token, oauth_client, *,
                     redirect_uri="https://app.example.com/cb",
                     challenge=None, method=None):
    params = {
        "client_id": oauth_client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }
    if challenge is not None:
        params["code_challenge"] = challenge
    if method is not None:
        params["code_challenge_method"] = method
    return await client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_token}"},
        params=params,
        follow_redirects=False,
    )


class TestIsPublicEnforcement:
    async def test_public_client_without_challenge_rejected(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """is_public=True без code_challenge → 403 PKCE_REQUIRED."""
        cc = await _make_client(
            client, admin_token, dept_a.id, name="pub_no_pkce", is_public=True,
        )
        resp = await _authorize(client, user_a_token, cc)
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PKCE_REQUIRED"

    async def test_public_client_with_plain_rejected(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """is_public=True + method=plain → 403 PKCE_METHOD_INVALID."""
        cc = await _make_client(
            client, admin_token, dept_a.id, name="pub_plain", is_public=True,
        )
        resp = await _authorize(
            client, user_a_token, cc,
            challenge="any-string-here", method="plain",
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PKCE_METHOD_INVALID"

    async def test_public_client_with_default_method_rejected(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """is_public=True + challenge без method (default=plain) → 403."""
        cc = await _make_client(
            client, admin_token, dept_a.id, name="pub_default", is_public=True,
        )
        resp = await _authorize(
            client, user_a_token, cc,
            challenge="some-challenge-string-no-method",
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PKCE_METHOD_INVALID"

    async def test_public_client_with_s256_accepted(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """is_public=True + S256 challenge → 302 (happy path)."""
        verifier = "valid-verifier-with-enough-length-zzzzzzz1234567890ab"
        challenge = _s256(verifier)
        cc = await _make_client(
            client, admin_token, dept_a.id, name="pub_s256", is_public=True,
        )
        resp = await _authorize(
            client, user_a_token, cc, challenge=challenge, method="S256",
        )
        assert resp.status_code == 302, resp.text
        code = resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]

        # Exchange кода с правильным verifier — 200.
        token_resp = await client.post(TOKEN_URL, json={
            "grant_type": "authorization_code",
            "client_id": cc["client_id"],
            "client_secret": cc["client_secret"],
            "code": code,
            "redirect_uri": "https://app.example.com/cb",
            "code_verifier": verifier,
        })
        assert token_resp.status_code == 200, token_resp.text


class TestConfidentialBackwardCompat:
    async def test_confidential_without_pkce_still_works(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """is_public=False (default) + без PKCE — back-compat."""
        cc = await _make_client(
            client, admin_token, dept_a.id, name="conf_no_pkce", is_public=False,
        )
        resp = await _authorize(client, user_a_token, cc)
        assert resp.status_code == 302, resp.text

    async def test_confidential_with_plain_still_works(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """is_public=False + plain PKCE — back-compat (поведение прежнее)."""
        cc = await _make_client(
            client, admin_token, dept_a.id, name="conf_plain", is_public=False,
        )
        resp = await _authorize(
            client, user_a_token, cc,
            challenge="plain-challenge-string-old-client",
            method="plain",
        )
        assert resp.status_code == 302, resp.text

    async def test_is_public_default_is_false(self, client, admin_token, dept_a):
        """Sanity: новый клиент без `is_public` создаётся как confidential."""
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "default_conf",
                "grant_types": ["authorization_code"],
                "redirect_uris": ["https://app.example.com/cb"],
                "allowed_scopes": [],
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_public"] is False
