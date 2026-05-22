"""Edge cases для `GET /oauth2/authorize` и `POST /oauth2/token`.

Базовые happy/redirect-mismatch/unknown-client тесты лежат в test_clients.py —
здесь только пропущенные ветви: state escape, отсутствие Bearer, истёкший
authorization code, grant_type не разрешён клиенту для authorize.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from src.core.security import hash_opaque_token
from src.models import OAuthAuthorizationCode

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"


async def _make_client(http_client, token, dept_id, *, name="ec_app",
                       grant_types=None, redirect_uris=None, scopes=None):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": name,
            "department_id": dept_id,
            "grant_types": grant_types or ["authorization_code"],
            "redirect_uris": redirect_uris or ["https://app.example.com/callback"],
            "allowed_scopes": scopes or [],
        },
    )
    return resp.json()


# ── /authorize — Bearer/auth ─────────────────────────────────────────────────

class TestAuthorizeAuth:
    async def test_without_bearer_returns_401(self, client, admin_token, dept_a):
        cl = await _make_client(client, admin_token, dept_a.id, name="no_bearer")
        resp = await client.get(
            AUTHORIZE_URL,
            params={"client_id": cl["client_id"],
                    "redirect_uri": "https://app.example.com/callback"},
            follow_redirects=False,
        )
        assert resp.status_code == 401

    async def test_with_invalid_bearer_returns_401(self, client, admin_token, dept_a):
        cl = await _make_client(client, admin_token, dept_a.id, name="bad_bearer")
        resp = await client.get(
            AUTHORIZE_URL,
            params={"client_id": cl["client_id"],
                    "redirect_uri": "https://app.example.com/callback"},
            headers={"Authorization": "Bearer garbage.invalid.token"},
            follow_redirects=False,
        )
        assert resp.status_code == 401


# ── /authorize — state escaping ──────────────────────────────────────────────

class TestAuthorizeStateEscape:
    async def test_state_with_special_chars_is_percent_encoded(
        self, client, admin_token, user_a_token, dept_a,
    ):
        cl = await _make_client(
            client, admin_token, dept_a.id, name="state_special",
            redirect_uris=["https://app.example.com/cb"],
        )
        suspicious_state = "csrf=абв&injected param/?#"
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                "state": suspicious_state,
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        # Spec-символы НЕ должны попасть в URL «как есть» — quote(safe='') экранирует всё.
        assert "&injected param/" not in location
        assert "%26injected%20param%2F" in location or "%26injected" in location
        # Сам параметр state остаётся доставленным
        assert "state=" in location

    async def test_state_omitted_when_not_provided(
        self, client, admin_token, user_a_token, dept_a,
    ):
        cl = await _make_client(
            client, admin_token, dept_a.id, name="no_state",
            redirect_uris=["https://app.example.com/cb"],
        )
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "state=" not in resp.headers["location"]

    async def test_state_at_2048_bytes_accepted(
        self, client, admin_token, user_a_token, dept_a,
    ):
        cl = await _make_client(
            client, admin_token, dept_a.id, name="state_2k_ok",
            redirect_uris=["https://app.example.com/cb"],
        )
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                "state": "x" * 2048,
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    async def test_state_above_2048_bytes_rejected(
        self, client, admin_token, user_a_token, dept_a,
    ):
        cl = await _make_client(
            client, admin_token, dept_a.id, name="state_2k_over",
            redirect_uris=["https://app.example.com/cb"],
        )
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                "state": "x" * 2049,
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 422


# ── /authorize — grant_type not allowed ──────────────────────────────────────

class TestAuthorizeGrantTypeGuard:
    async def test_client_without_authorization_code_grant_returns_403(
        self, client, admin_token, user_a_token, dept_a,
    ):
        cl = await _make_client(
            client, admin_token, dept_a.id,
            name="cc_only_for_authz",
            grant_types=["client_credentials"],
            redirect_uris=["https://app.example.com/cb"],
        )
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "GRANT_TYPE_NOT_ALLOWED"


# ── /token (authorization_code) — expired code ───────────────────────────────

class TestExchangeExpiredCode:
    async def test_expired_authorization_code_returns_401(
        self, client, admin_token, user_a_token, dept_a, db,
    ):
        """Получаем код через /authorize, искусственно протухаем его в БД,
        пытаемся обменять — ожидаем 401 OAUTH_CODE_EXPIRED."""
        cl = await _make_client(
            client, admin_token, dept_a.id, name="expiring_app",
            grant_types=["authorization_code"],
            redirect_uris=["https://app.example.com/expired"],
        )
        authorize = await client.get(
            AUTHORIZE_URL,
            params={"client_id": cl["client_id"],
                    "redirect_uri": "https://app.example.com/expired"},
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert authorize.status_code == 302
        location = authorize.headers["location"]
        raw_code = location.split("code=", 1)[1].split("&", 1)[0]

        # Двинем expires_at в прошлое.
        await db.execute(
            update(OAuthAuthorizationCode)
            .where(OAuthAuthorizationCode.code_hash == hash_opaque_token(raw_code))
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=5))
        )
        await db.commit()

        resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": cl["client_id"],
                "client_secret": cl["client_secret"],
                "code": raw_code,
                "redirect_uri": "https://app.example.com/expired",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "OAUTH_CODE_EXPIRED"


# ── /authorize — response_type validation ────────────────────────────────────
#
# RFC 6749 §3.1.1 + §4.1.2.1: только `code` flow поддерживается. Implicit
# (`token`) и hybrid (`id_token`, `code id_token`, ...) flow СОЗНАТЕЛЬНО
# не реализованы — implicit отдаёт access_token прямо в URL fragment
# (history-leak / referer-leak), hybrid требует OIDC, которого мы не делаем.
#
# Любой неизвестный response_type → 400 UNSUPPORTED_RESPONSE_TYPE. До фикса
# `response_type: str = Query(default="code")` без валидации тихо игнорировал
# `token`/`id_token` и всё равно отдавал code-flow.


class TestAuthorizeResponseType:
    """`response_type` ∈ {"code"}; всё остальное — 400 UNSUPPORTED_RESPONSE_TYPE."""

    async def test_response_type_code_is_accepted(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Sanity: `response_type=code` — 302 (happy path не сломан)."""
        cl = await _make_client(
            client, admin_token, dept_a.id, name="rt_code_ok",
            redirect_uris=["https://app.example.com/cb"],
        )
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                "response_type": "code",
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text

    async def test_response_type_default_is_accepted(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """`response_type` опущен — fastapi подставит default=code → 302.

        Регрессия-чек: валидатор должен срабатывать ТОЛЬКО на «странных»
        значениях, не на default-кейсе.
        """
        cl = await _make_client(
            client, admin_token, dept_a.id, name="rt_default_ok",
            redirect_uris=["https://app.example.com/cb"],
        )
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                # response_type намеренно опущен — берётся default="code"
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text

    @pytest.mark.parametrize(
        "bad_response_type",
        [
            "token",          # implicit flow — leak в URL fragment
            "id_token",       # OIDC, не реализован
            "code id_token",  # hybrid flow
            "code token",     # hybrid flow
            "none",           # OIDC «без response»
            "password",       # явно левый
            "",               # пустая строка
            "CODE",           # case-sensitivity (RFC §3.1.1)
        ],
    )
    async def test_response_type_other_returns_400(
        self, client, admin_token, user_a_token, dept_a, bad_response_type,
    ):
        """`response_type ∉ {"code"}` → 400 UNSUPPORTED_RESPONSE_TYPE (RFC 6749 §4.1.2.1)."""
        cl = await _make_client(
            client, admin_token, dept_a.id,
            name=f"rt_bad_{abs(hash(bad_response_type)) % 100000}",
            redirect_uris=["https://app.example.com/cb"],
        )
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                "response_type": bad_response_type,
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error_code"] == "UNSUPPORTED_RESPONSE_TYPE"
        # Сообщение должно фиксировать, что именно пришло — это помогает
        # клиенту понять причину (а не догадываться по generic 400).
        assert "response_type" in body["message"]

    async def test_response_type_validation_happens_before_code_issuance(
        self, client, admin_token, user_a_token, dept_a, db,
    ):
        """Bad `response_type` НЕ должен записывать `OAuthAuthorizationCode` в БД.

        Это инвариант: 400 на валидации — нет side-effect'ов в БД.
        Если кто-то поменяет порядок (сначала issue, потом валидация), этот
        тест поймает (state leak), и заодно — пустая запись о выписке кода с
        бракованным response_type в audit-trail.
        """
        from sqlalchemy import select

        cl = await _make_client(
            client, admin_token, dept_a.id, name="rt_no_side_effect",
            redirect_uris=["https://app.example.com/cb"],
        )
        before = await db.execute(select(OAuthAuthorizationCode))
        before_n = len(list(before.scalars().all()))

        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                "response_type": "token",
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

        after = await db.execute(select(OAuthAuthorizationCode))
        after_n = len(list(after.scalars().all()))
        assert before_n == after_n, "bad response_type must not issue a code"
