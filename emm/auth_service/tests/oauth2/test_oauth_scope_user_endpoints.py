"""OAuth-scope-creep на user-endpoint'ах (in-process guard).

До фикса `_identity_from_user_jwt` не знал об `oauth_scopes` из payload —
authorization_code JWT с узким scope (`svc_a`) проходил через `/me`,
`/tokens` POST, `/bots/*` с полными правами юзера. Introspect фильтровал,
in-process — нет (асимметрия).

Здесь проверяем:

1. `GET /users/me` под узко-scoped OAuth-JWT отдаёт `allowed_services`,
   обрезанные ровно по scope (а не live-полные права юзера).
2. `POST /tokens` (PAT-create) — 403 OAUTH_SCOPE_INSUFFICIENT, если
   `auth_service` нет в approved scope'ах.
3. `POST /bots` / прочие /bots/* — 403 OAUTH_SCOPE_INSUFFICIENT, если
   `auth_service` нет в approved scope'ах.
4. Те же ручки под обычным login-JWT — пропуск (не-OAuth токен).
5. OAuth-JWT с `auth_service` в scope'е — пропуск в PAT/bots ручки.
"""

import pytest_asyncio

from tests.conftest import _assign_role, _grant_service, _login, _make_service, _make_user
from datetime import timedelta
from src.utils.time import utcnow

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"
ME_URL = "/api/auth/v1/me"
TOKENS_URL = "/api/auth/v1/tokens"
BOTS_URL = "/api/auth/v1/bots"


async def _create_authcode_client(client, admin_token, dept_id, name, scopes):
    resp = await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/cb"],
            "allowed_scopes": scopes,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _run_code_flow(client, user_token, oauth_client, scope_str):
    auth_resp = await client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_token}"},
        params={
            "client_id": oauth_client["client_id"],
            "redirect_uri": "https://app.example.com/cb",
            "response_type": "code",
            "scope": scope_str,
        },
        follow_redirects=False,
    )
    assert auth_resp.status_code == 302, auth_resp.text
    code = auth_resp.headers["location"].split("code=")[1].split("&")[0]

    token_resp = await client.post(
        TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": oauth_client["client_id"],
            "client_secret": oauth_client["client_secret"],
            "code": code,
            "redirect_uri": "https://app.example.com/cb",
        },
    )
    assert token_resp.status_code == 200, token_resp.text
    return token_resp.json()["access_token"]


@pytest_asyncio.fixture()
async def user_multi_service(db, dept_a):
    """Юзер в dept_a с access к {svc_a, svc_b, auth_service}, роль reader везде."""
    for svc_name in ("svc_a", "svc_b", "auth_service"):
        await _make_service(db, svc_name)
        await _grant_service(db, dept_a.id, svc_name)
    user = await _make_user(
        db, "t_oauth_user", "User12345678!", department_id=dept_a.id,
    )
    for svc_name in ("svc_a", "svc_b", "auth_service"):
        await _assign_role(db, user.id, svc_name, "reader")
    await db.commit()
    return user


@pytest_asyncio.fixture()
async def user_multi_token(client, user_multi_service):
    return await _login(client, "t_oauth_user", "User12345678!")


# ── 1. /me видит только scope-разрешённые сервисы ────────────────────────────


class TestMeRespectsOAuthScope:
    async def test_narrow_scope_oauth_jwt_me_sees_only_scoped_service(
        self, client, admin_token, dept_a, user_multi_service, user_multi_token,
    ):
        """OAuth JWT с scope=svc_a → /me возвращает allowed_services == [svc_a].

        Юзер фактически имеет {svc_a, svc_b, auth_service}; узкий scope
        обязан резать вывод guard'а `_identity_from_user_jwt` симметрично
        introspect'у.
        """
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="narrow_scope_app",
            scopes=["svc_a"],
        )
        access = await _run_code_flow(
            client, user_multi_token, oauth_client, scope_str="svc_a",
        )

        me_resp = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {access}"},
        )
        assert me_resp.status_code == 200, me_resp.text
        body = me_resp.json()
        assert body["allowed_services"] == ["svc_a"]
        assert "svc_b" not in body["allowed_services"]
        assert "auth_service" not in body["allowed_services"]
        assert "svc_b" not in body["service_roles"]

    async def test_login_jwt_me_has_full_services(
        self, client, user_multi_service, user_multi_token,
    ):
        """Sanity: обычный login-JWT (oauth_scopes=None) видит все live-сервисы."""
        me_resp = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {user_multi_token}"},
        )
        assert me_resp.status_code == 200
        services = set(me_resp.json()["allowed_services"])
        assert {"svc_a", "svc_b", "auth_service"} <= services


# ── 2. PAT-create блокируется без auth_service scope ──────────────────────────


class TestPatCreateScopeGuard:
    async def test_oauth_jwt_without_auth_scope_cannot_create_pat(
        self, client, admin_token, dept_a, user_multi_service, user_multi_token,
    ):
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="no_auth_scope_app",
            scopes=["svc_a"],
        )
        access = await _run_code_flow(
            client, user_multi_token, oauth_client, scope_str="svc_a",
        )

        pat_resp = await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {access}"},
            json={"name": "scoped_pat", "allowed_services": ["svc_a"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        )
        assert pat_resp.status_code == 403, pat_resp.text
        assert pat_resp.json()["error_code"] == "OAUTH_SCOPE_INSUFFICIENT"

    async def test_oauth_jwt_with_auth_scope_can_create_pat(
        self, client, admin_token, dept_a, user_multi_service, user_multi_token,
    ):
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="with_auth_scope_app",
            scopes=["svc_a", "auth_service"],
        )
        access = await _run_code_flow(
            client, user_multi_token, oauth_client,
            scope_str="svc_a auth_service",
        )

        pat_resp = await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {access}"},
            json={"name": "ok_pat", "allowed_services": ["svc_a"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        )
        assert pat_resp.status_code == 201, pat_resp.text

    async def test_login_jwt_can_create_pat(self, client, user_multi_token):
        """Обычный login-JWT не должен попадать под scope-guard."""
        pat_resp = await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {user_multi_token}"},
            json={"name": "login_pat", "allowed_services": ["svc_a"], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        )
        assert pat_resp.status_code == 201, pat_resp.text


# ── 3. /bots/* блокируется без auth_service scope ────────────────────────────


@pytest_asyncio.fixture()
async def dept_admin_multi(db, dept_a):
    """department_admin в dept_a c access к {svc_a, auth_service}."""
    for svc_name in ("svc_a", "auth_service"):
        await _make_service(db, svc_name)
        await _grant_service(db, dept_a.id, svc_name)
    user = await _make_user(
        db, "t_da_oauth", "Admin12345678!",
        department_id=dept_a.id, platform_role="department_admin",
    )
    await db.commit()
    return user


@pytest_asyncio.fixture()
async def dept_admin_multi_token(client, dept_admin_multi):
    return await _login(client, "t_da_oauth", "Admin12345678!")


class TestBotsRouterScopeGuard:
    async def test_oauth_jwt_without_auth_scope_cannot_create_bot(
        self, client, admin_token, dept_a, dept_admin_multi, dept_admin_multi_token,
    ):
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="no_auth_bot_app",
            scopes=["svc_a"],
        )
        access = await _run_code_flow(
            client, dept_admin_multi_token, oauth_client, scope_str="svc_a",
        )

        bot_resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {access}"},
            json={
                "name": "blocked_bot",
                "department_id": dept_a.id,
                "allowed_services": ["svc_a"],
            },
        )
        assert bot_resp.status_code == 403, bot_resp.text
        assert bot_resp.json()["error_code"] == "OAUTH_SCOPE_INSUFFICIENT"

    async def test_oauth_jwt_without_auth_scope_cannot_list_bots(
        self, client, admin_token, dept_a, dept_admin_multi, dept_admin_multi_token,
    ):
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="no_auth_botlist_app",
            scopes=["svc_a"],
        )
        access = await _run_code_flow(
            client, dept_admin_multi_token, oauth_client, scope_str="svc_a",
        )

        list_resp = await client.get(
            BOTS_URL, headers={"Authorization": f"Bearer {access}"},
        )
        assert list_resp.status_code == 403
        assert list_resp.json()["error_code"] == "OAUTH_SCOPE_INSUFFICIENT"

    async def test_oauth_jwt_with_auth_scope_can_list_bots(
        self, client, admin_token, dept_a, dept_admin_multi, dept_admin_multi_token,
    ):
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="with_auth_botlist_app",
            scopes=["auth_service"],
        )
        access = await _run_code_flow(
            client, dept_admin_multi_token, oauth_client, scope_str="auth_service",
        )

        list_resp = await client.get(
            BOTS_URL, headers={"Authorization": f"Bearer {access}"},
        )
        assert list_resp.status_code == 200, list_resp.text

    async def test_login_jwt_can_use_bots_router(
        self, client, dept_admin_multi, dept_admin_multi_token, dept_a,
    ):
        """Обычный login-JWT (oauth_scopes=None) — scope-guard пропускает."""
        list_resp = await client.get(
            BOTS_URL, headers={"Authorization": f"Bearer {dept_admin_multi_token}"},
        )
        assert list_resp.status_code == 200
