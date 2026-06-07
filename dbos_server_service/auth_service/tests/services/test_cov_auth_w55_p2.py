"""COV для пробелов из аудита auth_service.

Закрывает четыре пункта:

1. `login` для платформенных `loging_admin` / `loging_reader` — HTTP-путь
   `_build_identity` для юзера без `department_id` возвращает пустые
   `allowed_services` / `service_roles` и корректную `platform_role`. Создание
   таких юзеров уже покрыто в `users/test_create.py`, login — нет.

2. `issue_authorization_code` — `effective_scopes = scopes ∩ allowed_scopes`
   на `/authorize`. До exchange'а scope режется уже на этапе выдачи кода;
   introspect-уровень покрыт, но факт обрезания на `/authorize` отдельно — нет.

3. `exchange_code` для public-клиента игнорирует `client_secret` по дизайну.
   Public flow аутентифицируется через PKCE; secret из body не должен ни
   валидироваться, ни блокировать обмен. `is_public_pkce.py` покрывает
   exchange БЕЗ secret'а — здесь фиксируем именно «передан мусорный secret,
   игнорится, обмен проходит».

4. `collect_user_permissions(oauth_scopes=[])` — unit-вызов с пустым списком
   (отличается от `None`): фильтр режет ВСЁ. Через introspect-уровень покрыто
   косвенно, прямой unit'а на функцию не было.
"""

import base64
import hashlib

import pytest
import pytest_asyncio

from src.services import auth_service
from tests.conftest import _login

LOGIN_URL = "/api/auth/v1/login"
CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── 1. loging_admin / loging_reader login HTTP ────────────────────────────────


@pytest_asyncio.fixture()
async def loging_admin_user(db):
    from tests.conftest import _make_user
    return await _make_user(
        db, "t_loging_admin", "LogAdmin1!", platform_role="loging_admin",
    )


@pytest_asyncio.fixture()
async def loging_reader_user(db):
    from tests.conftest import _make_user
    return await _make_user(
        db, "t_loging_reader", "LogRead1!", platform_role="loging_reader",
    )


class TestPlatformReadersLogin:
    async def test_loging_admin_login_returns_empty_services(
        self, client, loging_admin_user,
    ):
        resp = await client.post(
            LOGIN_URL, json={"username": "t_loging_admin", "password": "LogAdmin1!"},
        )
        assert resp.status_code == 200, resp.text
        identity = resp.json()["identity"]
        assert identity["platform_role"] == "loging_admin"
        assert identity["department_id"] is None
        assert identity["allowed_services"] == []
        assert identity["service_roles"] == {}

    async def test_loging_reader_login_returns_empty_services(
        self, client, loging_reader_user,
    ):
        resp = await client.post(
            LOGIN_URL, json={"username": "t_loging_reader", "password": "LogRead1!"},
        )
        assert resp.status_code == 200, resp.text
        identity = resp.json()["identity"]
        assert identity["platform_role"] == "loging_reader"
        assert identity["department_id"] is None
        assert identity["allowed_services"] == []
        assert identity["service_roles"] == {}


# ── 2. /authorize: scope ∩ client.allowed_scopes ──────────────────────────────


async def _make_authcode_client(client, admin_token, dept_id, *, name, allowed_scopes):
    resp = await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/cb"],
            "allowed_scopes": allowed_scopes,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _setup_user_with_three_services(db, dept_a):
    from tests.conftest import _assign_role, _grant_service, _make_service, _make_user

    for svc_name in ("svc_a", "svc_b", "svc_c"):
        await _make_service(db, svc_name)
        await _grant_service(db, dept_a.id, svc_name)
    user = await _make_user(
        db, "t_authorize_scope_filter", "User1234!", department_id=dept_a.id,
    )
    for svc_name in ("svc_a", "svc_b", "svc_c"):
        await _assign_role(db, user.id, svc_name, "reader")
    return user


class TestAuthorizeScopeIntersection:
    async def test_authorize_filters_scope_outside_allowed(
        self, client, admin_token, dept_a, db,
    ):
        """Клиент запросил `svc_a svc_b svc_unknown`, но `allowed_scopes=["svc_a"]`.
        Выдаётся код только для `svc_a` — `svc_b` и `svc_unknown` отрезаются.
        Проверяем через introspect: `allowed_services == ["svc_a"]`.
        """
        user = await _setup_user_with_three_services(db, dept_a)
        await db.commit()
        user_token = await _login(client, user.username, "User1234!")

        oauth_client = await _make_authcode_client(
            client, admin_token, dept_a.id,
            name="scope_filter_app",
            allowed_scopes=["svc_a"],
        )

        auth_resp = await client.get(
            AUTHORIZE_URL,
            headers={"Authorization": f"Bearer {user_token}"},
            params={
                "client_id": oauth_client["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                "response_type": "code",
                "scope": "svc_a svc_b svc_unknown",
            },
            follow_redirects=False,
        )
        assert auth_resp.status_code == 302, auth_resp.text
        code = auth_resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]

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
        access_token = token_resp.json()["access_token"]

        intro = await client.post(INTROSPECT_URL, json={"token": access_token})
        assert intro.status_code == 200
        body = intro.json()
        assert body["allowed_services"] == ["svc_a"]
        assert "svc_b" not in body["allowed_services"]
        assert "svc_unknown" not in body["allowed_services"]


# ── 3. Public-client exchange игнорирует переданный client_secret ─────────────


class TestPublicClientIgnoresClientSecret:
    async def test_public_client_exchange_ignores_wrong_secret(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Public-клиент: PKCE-S256 verifier правильный + `client_secret` мусорный
        в body → 200. Public flow секрет не валидирует (его и не должно быть),
        мусор в поле не блокирует обмен по дизайну.
        """
        verifier = "valid-verifier-with-enough-length-zzzzzzz1234567890ab"
        challenge = _s256(verifier)
        cc_resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "department_id": dept_a.id,
                "name": "pub_garbage_secret",
                "grant_types": ["authorization_code"],
                "redirect_uris": ["https://app.example.com/cb"],
                "allowed_scopes": [],
                "is_public": True,
            },
        )
        assert cc_resp.status_code == 201, cc_resp.text
        cc = cc_resp.json()

        auth_resp = await client.get(
            AUTHORIZE_URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
            params={
                "client_id": cc["client_id"],
                "redirect_uri": "https://app.example.com/cb",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert auth_resp.status_code == 302, auth_resp.text
        code = auth_resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]

        token_resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": cc["client_id"],
                "client_secret": "garbage-secret-should-be-ignored",
                "code": code,
                "redirect_uri": "https://app.example.com/cb",
                "code_verifier": verifier,
            },
        )
        assert token_resp.status_code == 200, token_resp.text
        assert "access_token" in token_resp.json()


# ── 4. collect_user_permissions(oauth_scopes=[]) ──────────────────────────────


class TestCollectUserPermissionsEmptyScopes:
    async def test_empty_oauth_scopes_returns_no_services(self, db, dept_a):
        """Пустой список scope'ов — НЕ то же, что None. None пропускает
        фильтрацию (значит non-OAuth-токен); `[]` режет всё, потому что
        intersect с пустым множеством — пустота.
        """
        from tests.conftest import (
            _assign_role,
            _grant_service,
            _make_service,
            _make_user,
        )

        for svc_name in ("svc_a", "svc_b"):
            await _make_service(db, svc_name)
            await _grant_service(db, dept_a.id, svc_name)
        user = await _make_user(
            db, "t_empty_scope_unit", "User1234!", department_id=dept_a.id,
        )
        for svc_name in ("svc_a", "svc_b"):
            await _assign_role(db, user.id, svc_name, "reader")
        await db.commit()

        # Sanity: без фильтрации видит оба сервиса.
        full_services, full_roles, _ = await auth_service.collect_user_permissions(
            db, user, oauth_scopes=None,
        )
        assert set(full_services) == {"svc_a", "svc_b"}
        assert set(full_roles.keys()) == {"svc_a", "svc_b"}

        # Пустой список — всё пусто.
        empty_services, empty_roles, empty_groups = await auth_service.collect_user_permissions(
            db, user, oauth_scopes=[],
        )
        assert empty_services == []
        assert empty_roles == {}
        assert empty_groups == {}

    async def test_partial_oauth_scopes_keeps_only_intersection(self, db, dept_a):
        """Частичное пересечение: scope-список содержит часть live-сервисов.
        Остаются только те, что есть и у юзера, и в scope'ах. То, что в одном
        и нет в другом, — выкидывается. Покрывает строки 204-205 (фильтр по
        allowed_services и service_roles).
        """
        from tests.conftest import (
            _assign_role,
            _grant_service,
            _make_service,
            _make_user,
        )

        for svc_name in ("svc_p1", "svc_p2", "svc_p3"):
            await _make_service(db, svc_name)
            await _grant_service(db, dept_a.id, svc_name)
        user = await _make_user(
            db, "t_partial_scope_unit", "User1234!", department_id=dept_a.id,
        )
        for svc_name in ("svc_p1", "svc_p2", "svc_p3"):
            await _assign_role(db, user.id, svc_name, "reader")
        await db.commit()

        # Scope-список покрывает 2 из 3 live-сервисов + один не-выданный.
        scopes = ["svc_p1", "svc_p3", "svc_not_granted"]
        services, roles, _groups = await auth_service.collect_user_permissions(
            db, user, oauth_scopes=scopes,
        )
        assert set(services) == {"svc_p1", "svc_p3"}, (
            f"intersection should keep svc_p1+svc_p3, drop svc_p2 and svc_not_granted, got {services}"
        )
        assert set(roles.keys()) == {"svc_p1", "svc_p3"}
        assert "svc_p2" not in roles
