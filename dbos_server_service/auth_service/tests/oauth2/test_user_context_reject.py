"""Тесты: OAuth `client_credentials` JWT отвергается на user-facing endpoint'ах.

Закрывает класс багов «OAuth actor_type=oauth_client через /oauth2/authorize →
audit-injection + FK violation».

`get_current_identity` dispatch'ит по `actor_type` и пропускает `oauth_client`-JWT
(нужно для `introspect`). Но user-facing handler'ы этим могут злоупотреблять:
`GET /oauth2/authorize` пишет `OAuthAuthorizationCode.user_id="cli_*"` и
audit-эвент с `actor_id="cli_*"` (audit-injection + потенциальный FK violation
на `users.id`).

Фикс: новый guard `require_user_context` режет m2m-identity с 403
`USER_CONTEXT_REQUIRED` на любом endpoint'е, который не отмечен явно как
m2m-friendly. Endpoint'ы под `require_service_token` (introspect /
service-access) — отдельная плоскость, их guard не затрагивает.

Что проверяем:
* cc-JWT → `GET /oauth2/authorize` → 403 USER_CONTEXT_REQUIRED.
* cc-JWT → `GET /me` → 403 (раньше попадал в handler).
* cc-JWT → `POST /users` (account_admin-only endpoint) → 403
  USER_CONTEXT_REQUIRED (не ROLE_REQUIRED — guard срабатывает раньше).
* cc-JWT → `POST /tokens` → 403.
* cc-JWT → `POST /authorization/introspect` — НЕ задевает guard
  (introspect живёт под `require_service_token`).
* User-JWT → те же endpoint'ы — поведение прежнее (sanity).
"""

import jwt

from src.core.config import get_settings

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
ME_URL = "/api/auth/v1/me"
USERS_URL = "/api/auth/v1/users"
TOKENS_URL = "/api/auth/v1/tokens"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _create_m2m_client(client, admin_token, dept_id, name, scopes):
    resp = await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["client_credentials"],
            "redirect_uris": [],
            "allowed_scopes": scopes,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_authcode_client(client, admin_token, dept_id, name):
    resp = await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/cb"],
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _issue_cc_token(client, client_id, client_secret):
    resp = await client.post(
        TOKEN_URL,
        json={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _decode_unverified(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


# ── 1. cc-JWT отвергается на user-facing endpoint'ах ─────────────────────────


class TestOauthClientRejectedOnUserEndpoints:
    """Каждый user-facing endpoint должен отвечать 403 USER_CONTEXT_REQUIRED
    для m2m JWT, не доходя до handler'а."""

    async def test_oauth_authorize_rejects_cc_jwt(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        """cc-JWT на `GET /oauth2/authorize` → 403, без audit-injection и без
        записи OAuthAuthorizationCode (FK violation / DB-junk)."""
        attacker = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cli_attacker",
            scopes=[service_x.service_name],
        )
        # Жертва: отдельный authorization_code-клиент, на которого атакующий
        # пытается выпустить code от своего имени.
        victim = await _create_authcode_client(
            client, admin_token, dept_a_with_service.id, name="victim_app",
        )
        cc_token = await _issue_cc_token(
            client, attacker["client_id"], attacker["client_secret"],
        )
        # Sanity: payload действительно m2m.
        assert _decode_unverified(cc_token)["actor_type"] == "oauth_client"

        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": victim["client_id"],
                "redirect_uri": "https://app.example.com/cb",
            },
            headers={"Authorization": f"Bearer {cc_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "USER_CONTEXT_REQUIRED"

    async def test_me_rejects_cc_jwt(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        """cc-JWT на `GET /me` → 403, не 200 (раньше доходил до handler'а и
        ломался на user_repo.get_by_id('cli_*'))."""
        cl = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_me_reject",
            scopes=[service_x.service_name],
        )
        cc_token = await _issue_cc_token(client, cl["client_id"], cl["client_secret"])

        resp = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {cc_token}"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "USER_CONTEXT_REQUIRED"

    async def test_create_user_rejects_cc_jwt(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        """cc-JWT на `POST /users` → 403 USER_CONTEXT_REQUIRED.

        Endpoint требует account_admin (`AccountAdmin`-guard). Раньше для
        oauth_client отказ был бы ROLE_REQUIRED (platform_role=None). Теперь
        `require_user_context` срабатывает раньше — сам факт m2m-JWT режется
        до проверки роли. Это важно: m2m-JWT не должен даже косвенно
        фигурировать как «actor пытался создать юзера».
        """
        cl = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_create_user_reject",
            scopes=[service_x.service_name],
        )
        cc_token = await _issue_cc_token(client, cl["client_id"], cl["client_secret"])

        resp = await client.post(
            USERS_URL,
            headers={"Authorization": f"Bearer {cc_token}"},
            json={"username": "should_not_exist", "password": "Pass12345678!",
                  "platform_role": "account_admin"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "USER_CONTEXT_REQUIRED"

    async def test_create_pat_rejects_cc_jwt(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        """cc-JWT на `POST /tokens` (PAT create) → 403.

        PAT — личный токен юзера; m2m-клиент не может «иметь PAT» по
        смыслу domain model. До фикса handler пытался создать
        PersonalAccessToken с `user_id="cli_*"` → FK-violation на users.id.
        """
        cl = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_create_pat_reject",
            scopes=[service_x.service_name],
        )
        cc_token = await _issue_cc_token(client, cl["client_id"], cl["client_secret"])

        resp = await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {cc_token}"},
            json={"name": "attacker_pat", "allowed_services": ["service_x"]},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "USER_CONTEXT_REQUIRED"


# ── 2. introspect под `require_service_token` — отдельная плоскость ──────────


class TestIntrospectNotAffected:
    """`/authorization/introspect` живёт под `require_service_token` (shared
    secret), а не под `require_user_context`. m2m-JWT — нормальный input в
    introspect, его задача — описать любой токен. Гард на user-context
    не должен задевать эту плоскость.
    """

    async def test_introspect_accepts_cc_jwt(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        cl = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_introspect_unaffected",
            scopes=[service_x.service_name],
        )
        cc_token = await _issue_cc_token(client, cl["client_id"], cl["client_secret"])

        # client-fixture автоматически проставляет SERVICE_API_KEY-Bearer для
        # /authorization/* — этот тест проверяет именно «введённый guard не
        # перехватывает introspect-плоскость».
        resp = await client.post(INTROSPECT_URL, json={"token": cc_token})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["active"] is True
        assert body["subject_type"] == "oauth_client"
        assert body["sub"] == cl["client_id"]


# ── 3. User-JWT — sanity: ничего не сломано ──────────────────────────────────


class TestUserJwtUnaffected:
    """Контрольная группа: обычный user-JWT (login или admin_token) проходит
    те же endpoint'ы как раньше — guard не задевает user-context."""

    async def test_me_with_user_jwt_still_200(self, client, user_a_token):
        resp = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 200, resp.text

    async def test_create_user_with_admin_jwt_still_201(self, client, admin_token):
        resp = await client.post(
            USERS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"username": "sanity_user_after_fix", "password": "Pass12345678!",
                  "platform_role": "account_admin"},
        )
        assert resp.status_code == 201, resp.text

    async def test_create_pat_with_user_jwt_still_201(self, client, user_a_token):
        resp = await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "sanity_pat_after_fix", "allowed_services": ["service_x"]},
        )
        assert resp.status_code == 201, resp.text
