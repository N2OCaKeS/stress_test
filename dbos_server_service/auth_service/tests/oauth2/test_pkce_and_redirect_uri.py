"""Тесты: PKCE (RFC 7636) для authorization_code flow + валидация redirect_uri.

Что закрываем (OAuth2 hardening):

* PKCE: public clients (SPA/CLI) уязвимы к code interception без
  code_challenge/code_verifier. `authorize` принимает challenge,
  `exchange_code` верифицирует verifier.
* redirect_uri: pydantic-уровень бьёт http:// (кроме localhost) и fragment —
  фишинг-вектор «302 через trusted auth-домен → http-URL атакующего».

Что проверяем:
* PKCE happy path (S256): authorize с challenge → token exchange с verifier → 200.
* PKCE happy path (plain): aналогично с method=plain.
* PKCE mismatch: правильный challenge, неправильный verifier → 401 INVALID_GRANT.
* PKCE missing verifier: код выписан с challenge, exchange без verifier → 401.
* PKCE без challenge (legacy confidential client): exchange без verifier → 200
  (back-compat).
* PKCE unknown method → 403 PKCE_METHOD_INVALID.
* PKCE verifier при коде БЕЗ challenge: passthrough — verifier игнорируется
  (sanity, не security issue).
* http://-redirect_uri (не localhost) → 422.
* fragment в redirect_uri → 422.
* http://localhost — допустимо.
"""

import base64
import hashlib

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _s256(verifier: str) -> str:
    """Compute RFC 7636 §4.2 code_challenge from a verifier (S256)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def _create_authcode_client(
    http_client,
    admin_token,
    dept_id,
    *,
    name,
    redirect_uri="https://app.example.com/callback",
    scopes=None,
):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["authorization_code"],
            "redirect_uris": [redirect_uri],
            "allowed_scopes": scopes or [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _authorize(
    http_client,
    user_token,
    oauth_client,
    *,
    redirect_uri="https://app.example.com/callback",
    challenge=None,
    method=None,
    scope="",
):
    params = {
        "client_id": oauth_client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }
    if challenge is not None:
        params["code_challenge"] = challenge
    if method is not None:
        params["code_challenge_method"] = method
    if scope:
        params["scope"] = scope
    return await http_client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_token}"},
        params=params,
        follow_redirects=False,
    )


def _extract_code(location: str) -> str:
    return location.split("code=", 1)[1].split("&", 1)[0]


# ── 1. PKCE happy path ───────────────────────────────────────────────────────


class TestPKCEHappyPath:
    async def test_s256_full_flow_success(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Public client (SPA/CLI): authorize+S256 challenge → exchange+verifier → 200."""
        verifier = "this-is-a-cryptographically-random-verifier-1234567890"
        challenge = _s256(verifier)

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="pkce_s256_app",
        )
        auth_resp = await _authorize(
            client, user_a_token, oauth_client,
            challenge=challenge, method="S256",
        )
        assert auth_resp.status_code == 302, auth_resp.text
        code = _extract_code(auth_resp.headers["location"])

        token_resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": oauth_client["client_id"],
                "client_secret": oauth_client["client_secret"],
                "code": code,
                "redirect_uri": "https://app.example.com/callback",
                "code_verifier": verifier,
            },
        )
        assert token_resp.status_code == 200, token_resp.text
        body = token_resp.json()
        assert "access_token" in body
        assert body["token_type"] == "Bearer"

    async def test_plain_method_full_flow_success(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """`plain` method (RFC 7636 §4.3): challenge == verifier."""
        verifier = "plain-verifier-string-not-hashed-for-this-method-123456"
        challenge = verifier  # plain: identity

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="pkce_plain_app",
        )
        auth_resp = await _authorize(
            client, user_a_token, oauth_client,
            challenge=challenge, method="plain",
        )
        assert auth_resp.status_code == 302, auth_resp.text
        code = _extract_code(auth_resp.headers["location"])

        token_resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": oauth_client["client_id"],
                "client_secret": oauth_client["client_secret"],
                "code": code,
                "redirect_uri": "https://app.example.com/callback",
                "code_verifier": verifier,
            },
        )
        assert token_resp.status_code == 200, token_resp.text


# ── 2. PKCE failure modes ────────────────────────────────────────────────────


class TestPKCEFailureModes:
    async def test_s256_mismatched_verifier_returns_401(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Issue code с правильным challenge → exchange с не-тем verifier'ом → 401."""
        verifier = "correct-verifier-x" * 3
        challenge = _s256(verifier)

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="pkce_mismatch_app",
        )
        auth_resp = await _authorize(
            client, user_a_token, oauth_client,
            challenge=challenge, method="S256",
        )
        code = _extract_code(auth_resp.headers["location"])

        token_resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": oauth_client["client_id"],
                "client_secret": oauth_client["client_secret"],
                "code": code,
                "redirect_uri": "https://app.example.com/callback",
                "code_verifier": "wrong-verifier-no-match-at-all-zzz",
            },
        )
        assert token_resp.status_code == 401, token_resp.text
        assert token_resp.json()["error_code"] == "INVALID_GRANT"

    async def test_missing_verifier_when_challenge_set_returns_401(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Code выписан с challenge, exchange БЕЗ verifier → 401 INVALID_GRANT."""
        verifier = "abc-this-verifier-is-not-going-to-be-sent-on-exchange-x"
        challenge = _s256(verifier)

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="pkce_missing_app",
        )
        auth_resp = await _authorize(
            client, user_a_token, oauth_client,
            challenge=challenge, method="S256",
        )
        code = _extract_code(auth_resp.headers["location"])

        token_resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": oauth_client["client_id"],
                "client_secret": oauth_client["client_secret"],
                "code": code,
                "redirect_uri": "https://app.example.com/callback",
                # code_verifier намеренно отсутствует
            },
        )
        assert token_resp.status_code == 401, token_resp.text
        assert token_resp.json()["error_code"] == "INVALID_GRANT"

    async def test_plain_mismatched_verifier_returns_401(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Plain method: verifier != challenge → 401."""
        verifier = "plain-verifier-aaa"
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="pkce_plain_mis_app",
        )
        auth_resp = await _authorize(
            client, user_a_token, oauth_client,
            challenge=verifier, method="plain",
        )
        code = _extract_code(auth_resp.headers["location"])

        token_resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": oauth_client["client_id"],
                "client_secret": oauth_client["client_secret"],
                "code": code,
                "redirect_uri": "https://app.example.com/callback",
                "code_verifier": "different-string-bbb",
            },
        )
        assert token_resp.status_code == 401
        assert token_resp.json()["error_code"] == "INVALID_GRANT"

    async def test_unknown_method_rejected_at_authorize(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """`code_challenge_method=MD5` (или любой не-S256/plain) — 403 на authorize.

        Issue code'а с непредусмотренным методом — гарантированно неверифицируемый.
        Лучше упасть на authorize'e, чем позже на exchange.
        """
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="pkce_bad_method_app",
        )
        resp = await _authorize(
            client, user_a_token, oauth_client,
            challenge="some-challenge", method="MD5",
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PKCE_METHOD_INVALID"

    async def test_method_without_challenge_rejected(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """`code_challenge_method=S256` без `code_challenge` — 403.

        Метод без challenge'а не имеет смысла (RFC 7636 §4.3 описывает
        пару). До фикса метод тихо игнорировался и клиент мог не заметить,
        что PKCE фактически не активирован.
        """
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="pkce_method_no_challenge_app",
        )
        resp = await _authorize(
            client, user_a_token, oauth_client,
            challenge=None, method="S256",
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PKCE_CHALLENGE_REQUIRED"


# ── 3. Backward compat — confidential client без PKCE ───────────────────────


class TestPKCEBackwardCompat:
    async def test_legacy_flow_without_challenge_works(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Confidential client (с client_secret) без PKCE: authorize без
        challenge → exchange без verifier → 200. Back-compat обеспечен."""
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="legacy_no_pkce_app",
        )
        auth_resp = await _authorize(client, user_a_token, oauth_client)
        assert auth_resp.status_code == 302, auth_resp.text
        code = _extract_code(auth_resp.headers["location"])

        token_resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": oauth_client["client_id"],
                "client_secret": oauth_client["client_secret"],
                "code": code,
                "redirect_uri": "https://app.example.com/callback",
            },
        )
        assert token_resp.status_code == 200, token_resp.text

    async def test_verifier_passed_but_no_challenge_stored_succeeds(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Code БЕЗ challenge + verifier в exchange'е — verifier игнорируется,
        обмен идёт обычным confidential-путём (client_secret-only).

        Это не security issue: если challenge не был сохранён, значит client
        не запрашивал PKCE — наличие лишнего verifier'а в запросе ничего не
        ослабляет, проверять не от чего."""
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id, name="extra_verifier_app",
        )
        auth_resp = await _authorize(client, user_a_token, oauth_client)
        code = _extract_code(auth_resp.headers["location"])

        token_resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": oauth_client["client_id"],
                "client_secret": oauth_client["client_secret"],
                "code": code,
                "redirect_uri": "https://app.example.com/callback",
                "code_verifier": "verifier-that-no-one-asked-for",
            },
        )
        assert token_resp.status_code == 200, token_resp.text


# ── 4. redirect_uri validators on OAuthClientCreate ─────────────────────────


class TestRedirectUriValidators:
    async def test_http_non_localhost_returns_422(self, client, admin_token, dept_a):
        """`http://app.example.com/cb` без localhost — 422 на создании client'а.

        Защищает от phishing-вектора: атакующий регистрирует client с
        http-redirect, потом 302 через trusted auth-домен → MITM перехватит code.
        """
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "http_evil_app",
                "grant_types": ["authorization_code"],
                "redirect_uris": ["http://app.example.com/callback"],
                "allowed_scopes": [],
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_redirect_uri_with_fragment_returns_422(
        self, client, admin_token, dept_a,
    ):
        """`https://app.example.com/cb#section` (fragment) — 422.

        RFC 6749 §3.1.2: redirect_uri не может содержать fragment. Тёмные
        углы — SPA-router может интерпретировать `#?code=...` как fragment-
        route и отдать code в обход CSP/CORS правил.
        """
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "fragment_app",
                "grant_types": ["authorization_code"],
                "redirect_uris": ["https://app.example.com/cb#section"],
                "allowed_scopes": [],
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_localhost_http_is_allowed(self, client, admin_token, dept_a):
        """`http://localhost:8080/cb` — допустимо (native CLI dev).

        RFC 8252 §7.3 — loopback exception: native клиенты не могут безопасно
        получить TLS-сертификат для loopback'а, поэтому http://localhost — ок.
        """
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "localhost_dev_app",
                "grant_types": ["authorization_code"],
                "redirect_uris": ["http://localhost:8080/cb"],
                "allowed_scopes": [],
            },
        )
        assert resp.status_code == 201, resp.text

    async def test_127_loopback_http_is_allowed(self, client, admin_token, dept_a):
        """`http://127.0.0.1/cb` тоже допустим (RFC 8252)."""
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "loopback_v4_app",
                "grant_types": ["authorization_code"],
                "redirect_uris": ["http://127.0.0.1:9000/cb"],
                "allowed_scopes": [],
            },
        )
        assert resp.status_code == 201, resp.text

    async def test_https_normal_is_allowed(self, client, admin_token, dept_a):
        """Sanity: обычный https-URI без fragment — 201."""
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "https_app",
                "grant_types": ["authorization_code"],
                "redirect_uris": ["https://app.example.com/cb"],
                "allowed_scopes": [],
            },
        )
        assert resp.status_code == 201, resp.text

    async def test_mixed_list_with_one_bad_uri_returns_422(
        self, client, admin_token, dept_a,
    ):
        """Список из good + bad → 422 (валидатор бьёт по всему списку)."""
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "mixed_list_app",
                "grant_types": ["authorization_code"],
                "redirect_uris": [
                    "https://app.example.com/cb",
                    "http://evil.example.com/cb",
                ],
                "allowed_scopes": [],
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_ftp_scheme_returns_422(self, client, admin_token, dept_a):
        """Неизвестная схема (ftp, file, javascript) — 422."""
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "ftp_app",
                "grant_types": ["authorization_code"],
                "redirect_uris": ["ftp://app.example.com/cb"],
                "allowed_scopes": [],
            },
        )
        assert resp.status_code == 422, resp.text
