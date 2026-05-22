"""Тесты: introspect для OAuth2 `authorization_code` JWT — защита от scope-creep.

Что закрываем:

OAuth-scope-creep при revalidate. До фикса `exchange_code` выписывал JWT, в
котором `allowed_services` уже был сужен по `auth_code.scopes`, НО на повторном
introspect `authorization_service` делал `collect_user_permissions(db, user)`
БЕЗ знания исходного scope — и возвращал live-права юзера целиком. Third-party
app с `scope=["svc_a"]` через introspect внезапно видел `{svc_a, svc_b, svc_c}`.

Фикс: `exchange_code` кладёт в payload `oauth_scopes` (снапшот выданных scope'ов),
`authorization_service.introspect` пересекает live-права с этим набором.

Что проверяем:
* `exchange_code` кладёт `oauth_scopes` в JWT payload (а login-JWT — не кладёт).
* Юзер с access к {svc_a, svc_b, svc_c} → OAuth code flow с scopes=["svc_a"] →
  introspect → `allowed_services == ["svc_a"]` (а не все три).
* После revoke у юзера/dept'а access к `svc_a` → introspect возвращает `[]`
  (scope пересекается с live-правами, оба нужны).
* `oauth_scopes=[]` (пустой scope) → introspect → `allowed_services == []` (а не
  fall-back к полным правам юзера).
* `service-access`-эндпоинт уважает intersected `allowed_services`: запрос на
  svc_b с токеном scope=["svc_a"] → denied.
"""

import jwt
import pytest

from src.core.config import get_settings

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
ACCESS_URL = "/api/auth/v1/authorization/service-access"


def _decode_unverified(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


async def _create_authcode_client(client, admin_token, dept_id, name, scopes):
    resp = await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/callback"],
            "allowed_scopes": scopes,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _run_code_flow(client, user_token, oauth_client, scope_str):
    """User-driven authorization_code → exchange. Возвращает access_token."""
    auth_resp = await client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_token}"},
        params={
            "client_id": oauth_client["client_id"],
            "redirect_uri": "https://app.example.com/callback",
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
            "redirect_uri": "https://app.example.com/callback",
        },
    )
    assert token_resp.status_code == 200, token_resp.text
    return token_resp.json()["access_token"]


# ── multi-service фикстуры ─────────────────────────────────────────────────────


async def _setup_user_with_three_services(db, dept_a):
    """Юзер в dept_a с access к {svc_a, svc_b, svc_c} и ролью `reader` на каждом."""
    from tests.conftest import (
        _assign_role,
        _grant_service,
        _make_service,
        _make_user,
    )

    for svc_name in ("svc_a", "svc_b", "svc_c"):
        await _make_service(db, svc_name)
        await _grant_service(db, dept_a.id, svc_name)

    user = await _make_user(
        db, "t_oauth_scope_user", "User1234!",
        department_id=dept_a.id,
    )
    for svc_name in ("svc_a", "svc_b", "svc_c"):
        await _assign_role(db, user.id, svc_name, "reader")
    return user


# ── 1. Payload содержит oauth_scopes ──────────────────────────────────────────


class TestOAuthScopesInPayload:
    async def test_exchange_code_jwt_has_oauth_scopes(
        self, client, admin_token, dept_a, db,
    ):
        user = await _setup_user_with_three_services(db, dept_a)
        await db.commit()
        from tests.conftest import _login
        user_token = await _login(client, user.username, "User1234!")

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="scope_payload_app",
            scopes=["svc_a", "svc_b", "svc_c"],
        )
        access_token = await _run_code_flow(
            client, user_token, oauth_client, scope_str="svc_a",
        )
        payload = _decode_unverified(access_token)
        assert payload.get("actor_type") == "user"
        assert payload.get("oauth_scopes") == ["svc_a"]
        assert payload.get("oauth_client_id") == oauth_client["client_id"]

    async def test_login_jwt_has_no_oauth_scopes(self, user_a_token):
        """Обычный login-JWT — `oauth_scopes` отсутствует (None).

        Это критично: None в introspect означает «не OAuth, не фильтровать».
        Пустой список означал бы «фильтровать всё в пустоту».
        """
        payload = _decode_unverified(user_a_token)
        assert "oauth_scopes" not in payload or payload["oauth_scopes"] is None

    async def test_exchange_code_jwt_has_no_sensitive_claims(
        self, client, admin_token, dept_a, db,
    ):
        """JWT после exchange_code не должен содержать username / department_id /
        platform_role / allowed_services / service_roles. JWT декодируется без
        ключа (base64url) — утечка токена раскрыла бы PII и полный профиль прав.
        Эти поля пересчитываются introspect'ом из БД.
        """
        user = await _setup_user_with_three_services(db, dept_a)
        await db.commit()
        from tests.conftest import _login
        user_token = await _login(client, user.username, "User1234!")

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="no_sensitive_claims_app",
            scopes=["svc_a", "svc_b"],
        )
        access_token = await _run_code_flow(
            client, user_token, oauth_client, scope_str="svc_a svc_b",
        )
        payload = _decode_unverified(access_token)
        for claim in (
            "username",
            "department_id",
            "platform_role",
            "allowed_services",
            "service_roles",
        ):
            assert claim not in payload, (
                f"sensitive claim '{claim}' must not be in OAuth JWT payload"
            )
        # Sanity: минимальный набор — на месте.
        assert payload.get("sub") == user.id
        assert payload.get("actor_type") == "user"
        assert payload.get("oauth_client_id") == oauth_client["client_id"]
        assert payload.get("oauth_scopes") == ["svc_a", "svc_b"]


# ── 2. Scope-creep: introspect фильтрует по oauth_scopes ──────────────────────


class TestIntrospectScopeIntersection:
    async def test_introspect_narrow_scope_returns_only_scoped_service(
        self, client, admin_token, dept_a, db,
    ):
        """Юзер с access к {svc_a, svc_b, svc_c} → OAuth scope=["svc_a"] →
        introspect → allowed_services == ["svc_a"]. Без фикса вернулись бы все три."""
        user = await _setup_user_with_three_services(db, dept_a)
        await db.commit()
        from tests.conftest import _login
        user_token = await _login(client, user.username, "User1234!")

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="narrow_scope_app",
            scopes=["svc_a", "svc_b", "svc_c"],
        )
        access_token = await _run_code_flow(
            client, user_token, oauth_client, scope_str="svc_a",
        )

        resp = await client.post(INTROSPECT_URL, json={"token": access_token})
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is True
        assert body["subject_type"] == "user"
        # Главная проверка: ровно один сервис в allowed_services, а не все три.
        assert body["allowed_services"] == ["svc_a"]
        assert "svc_b" not in body["allowed_services"]
        assert "svc_c" not in body["allowed_services"]
        # service_roles тоже сужен — иначе клиент мог бы видеть имена ролей.
        assert set(body["service_roles"].keys()) == {"svc_a"}

    async def test_introspect_revoke_live_permission_returns_empty_intersection(
        self, client, admin_token, dept_a, db,
    ):
        """OAuth scope=["svc_a"], затем у юзера сняли роль на svc_a (или dept'у
        revoke'нули доступ) → introspect → allowed_services == []. Демонстрирует
        что мы пересекаем именно с LIVE-правами, а не верим issued-time snapshot."""
        from sqlalchemy import update

        from src.models import DepartmentServiceAccess

        user = await _setup_user_with_three_services(db, dept_a)
        await db.commit()
        from tests.conftest import _login
        user_token = await _login(client, user.username, "User1234!")

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="revoke_after_issue_app",
            scopes=["svc_a", "svc_b", "svc_c"],
        )
        access_token = await _run_code_flow(
            client, user_token, oauth_client, scope_str="svc_a",
        )

        # Sanity: до revoke токен видит svc_a (и только его).
        before = await client.post(INTROSPECT_URL, json={"token": access_token})
        assert before.json()["allowed_services"] == ["svc_a"]

        # Снимаем у dept'а доступ к svc_a — live-права юзера на svc_a → пусто.
        await db.execute(
            update(DepartmentServiceAccess)
            .where(
                DepartmentServiceAccess.department_id == dept_a.id,
                DepartmentServiceAccess.service_name == "svc_a",
            )
            .values(is_active=False)
        )
        await db.commit()

        after = await client.post(INTROSPECT_URL, json={"token": access_token})
        assert after.status_code == 200
        body = after.json()
        # Юзер живой, токен валидный по подписи, но пересечение
        # (svc_a из scope) ∩ (svc_b, svc_c из live-прав) == ∅.
        assert body["active"] is True
        assert body["allowed_services"] == []
        assert body["service_roles"] == {}

    async def test_introspect_empty_scope_returns_no_services(
        self, client, admin_token, dept_a, db,
    ):
        """OAuth scope=[] (юзер ничего не аппрувнул, либо клиент не запросил
        известных scope) → allowed_services == []. None ≠ [].
        """
        user = await _setup_user_with_three_services(db, dept_a)
        await db.commit()
        from tests.conftest import _login
        user_token = await _login(client, user.username, "User1234!")

        # Клиент допускает {svc_a, svc_b}, но юзер запрашивает пустой scope.
        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="empty_scope_app",
            scopes=["svc_a", "svc_b"],
        )
        access_token = await _run_code_flow(
            client, user_token, oauth_client, scope_str="",
        )
        payload = _decode_unverified(access_token)
        assert payload.get("oauth_scopes") == []

        resp = await client.post(INTROSPECT_URL, json={"token": access_token})
        body = resp.json()
        assert body["active"] is True
        assert body["allowed_services"] == []
        assert body["service_roles"] == {}


# ── 3. service-access: scope-фильтр распространяется на /service-access ───────


class TestServiceAccessRespectsOAuthScope:
    async def test_service_access_denied_for_out_of_scope_service(
        self, client, admin_token, dept_a, db,
    ):
        """Токен выдан со scope=["svc_a"]. Запрос service-access на svc_b
        должен быть denied — service_not_in_token (ведь introspect уже отдал
        suzhennyy allowed_services)."""
        user = await _setup_user_with_three_services(db, dept_a)
        await db.commit()
        from tests.conftest import _login
        user_token = await _login(client, user.username, "User1234!")

        oauth_client = await _create_authcode_client(
            client, admin_token, dept_a.id,
            name="svc_access_scoped_app",
            scopes=["svc_a", "svc_b"],
        )
        access_token = await _run_code_flow(
            client, user_token, oauth_client, scope_str="svc_a",
        )

        # svc_a — в scope → разрешено.
        ok = await client.post(
            ACCESS_URL,
            json={"subject_token": access_token, "service_name": "svc_a"},
        )
        assert ok.status_code == 200
        assert ok.json()["allowed"] is True

        # svc_b — у юзера есть live-права, но scope узкий → запрет.
        denied = await client.post(
            ACCESS_URL,
            json={"subject_token": access_token, "service_name": "svc_b"},
        )
        assert denied.status_code == 200
        assert denied.json()["allowed"] is False
