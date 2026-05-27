"""Тесты: introspect для OAuth2 `client_credentials` JWT.

JWT-ветка `introspect` revalidate'ит `sub` через DB. Для `client_credentials`
grant `sub == client.client_id` (`cli_*`), а не user_id — ищем такой JWT через
`OAuthClientRepository`, не `UserRepository`.
Dispatch — по `payload["actor_type"]`:
  * "user" / отсутствует → UserRepository
  * "oauth_client" → OAuthClientRepository

Что проверяем:
* `client_credentials_token` кладёт `actor_type="oauth_client"` в payload.
* Login (user-JWT) кладёт `actor_type="user"` в payload.
* `introspect` для client_credentials JWT:
    - active=True, subject_type="oauth_client", sub=client_id, scope/department корректны;
    - после удаления (deactivate) клиента → active=False;
    - после revoke service-access у отдела → сервис исчезает из allowed_services.
* `service-access` для client_credentials JWT работает (allowed=True по dept-доступу).
"""

import jwt

from src.core.config import get_settings

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
ACCESS_URL = "/api/auth/v1/authorization/service-access"


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
    """Декодируем JWT без проверки подписи — нам важно содержимое payload."""
    return jwt.decode(token, options={"verify_signature": False})


# ── 1. Payload содержит actor_type ────────────────────────────────────────────


class TestActorTypeInPayload:
    async def test_client_credentials_jwt_has_actor_type_oauth_client(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        created = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_actor_type_app",
            scopes=[service_x.service_name],
        )
        token = await _issue_cc_token(client, created["client_id"], created["client_secret"])
        payload = _decode_unverified(token)
        assert payload["actor_type"] == "oauth_client"
        assert payload["sub"] == created["client_id"]
        assert payload["sub"].startswith("cli_")

    async def test_user_jwt_has_actor_type_user(self, user_a_token):
        payload = _decode_unverified(user_a_token)
        assert payload.get("actor_type") == "user"
        assert payload["sub"].startswith("usr_")

    async def test_client_credentials_jwt_payload_has_no_sensitive_claims(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        """cc-JWT payload минимален: ни department_id, ни allowed_services, ни
        service_roles — иначе base64url-decode без ключа раскрыл бы привязку к
        отделу и список доступных сервисов. introspect берёт их из БД."""
        created = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_minimal_payload",
            scopes=[service_x.service_name],
        )
        token = await _issue_cc_token(client, created["client_id"], created["client_secret"])
        payload = _decode_unverified(token)

        assert "department_id" not in payload
        assert "allowed_services" not in payload
        assert "service_roles" not in payload
        # Минимум остаётся: sub + actor_type + стандартные claims.
        assert payload["sub"] == created["client_id"]
        assert payload["actor_type"] == "oauth_client"
        assert {"iat", "exp", "iss", "aud"} <= set(payload)


# ── 2. introspect: happy path ─────────────────────────────────────────────────


class TestOauthClientIntrospect:
    async def test_introspect_cc_jwt_active(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        """JWT, выданный по client_credentials, проходит introspect → active=True."""
        created = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_introspect_app",
            scopes=[service_x.service_name],
        )
        token = await _issue_cc_token(client, created["client_id"], created["client_secret"])

        resp = await client.post(INTROSPECT_URL, json={"token": token})
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is True
        assert body["subject_type"] == "oauth_client"
        assert body["sub"] == created["client_id"]
        assert body["department_id"] == dept_a_with_service.id
        assert service_x.service_name in body["allowed_services"]
        # service_roles для client_credentials всегда пуст — это m2m токен.
        assert body["service_roles"] == {}
        # platform_role / is_banned не относятся к OAuth-клиенту.
        assert body["platform_role"] is None
        assert body["is_banned"] is False

    async def test_introspect_cc_jwt_scopes_intersected_with_dept(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        """allowed_services = intersection (dept-сервисы, client.allowed_scopes).

        Клиент просит `service_x` и `unknown_service` — второй не выдан отделу
        и не должен оказаться в allowed_services.
        """
        created = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_scope_intersect_app",
            scopes=[service_x.service_name, "unknown_service"],
        )
        token = await _issue_cc_token(client, created["client_id"], created["client_secret"])
        resp = await client.post(INTROSPECT_URL, json={"token": token})
        body = resp.json()
        assert body["active"] is True
        assert service_x.service_name in body["allowed_services"]
        assert "unknown_service" not in body["allowed_services"]


# ── 3. introspect: client удалён / неактивен ──────────────────────────────────


class TestOauthClientRevocation:
    async def test_introspect_after_delete_client_returns_inactive(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        """После `DELETE /oauth2/clients/{id}` старый client_credentials JWT мёртв."""
        created = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_to_delete_app",
            scopes=[service_x.service_name],
        )
        token = await _issue_cc_token(client, created["client_id"], created["client_secret"])

        # Sanity: до удаления active=True.
        before = await client.post(INTROSPECT_URL, json={"token": token})
        assert before.json()["active"] is True

        # delete_client делает soft-deactivate: client.is_active=False.
        del_resp = await client.delete(
            f"{CLIENTS_URL}/{created['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert del_resp.status_code == 200

        after = await client.post(INTROSPECT_URL, json={"token": token})
        assert after.status_code == 200
        assert after.json()["active"] is False

    async def test_introspect_cc_jwt_for_unknown_client_inactive(
        self, client, dept_a_with_service, service_x
    ):
        """JWT валиден по подписи, но `sub` указывает на несуществующего клиента —
        introspect должен вернуть active=False, не падать на None."""
        from datetime import datetime, timedelta, timezone

        bogus_token = jwt.encode(
            {
                "sub": "cli_completely_unknown",
                "actor_type": "oauth_client",
                "department_id": dept_a_with_service.id,
                "allowed_services": [service_x.service_name],
                "service_roles": {},
                "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
            },
            get_settings().secret_key,
            algorithm="HS256",
        )
        resp = await client.post(INTROSPECT_URL, json={"token": bogus_token})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_department_service_access_revoked_after_cc_jwt(
        self, client, admin_token, dept_a_with_service, service_x, db
    ):
        """Отделу отозвали доступ к сервису → старый client_credentials JWT
        больше не показывает этот сервис в `allowed_services`."""
        from sqlalchemy import update

        from src.models import DepartmentServiceAccess

        created = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_dept_revoke_app",
            scopes=[service_x.service_name],
        )
        token = await _issue_cc_token(client, created["client_id"], created["client_secret"])

        before = await client.post(INTROSPECT_URL, json={"token": token})
        assert service_x.service_name in before.json()["allowed_services"]

        await db.execute(
            update(DepartmentServiceAccess)
            .where(
                DepartmentServiceAccess.department_id == dept_a_with_service.id,
                DepartmentServiceAccess.service_name == service_x.service_name,
            )
            .values(is_active=False)
        )
        await db.commit()

        after = await client.post(INTROSPECT_URL, json={"token": token})
        assert after.status_code == 200
        body = after.json()
        # client сам активен, но сервис отделу больше не доступен.
        assert body["active"] is True
        assert service_x.service_name not in body["allowed_services"]


# ── 4. service-access: m2m токен пускают по dept-доступу ──────────────────────


class TestServiceAccessForOauthClient:
    async def test_service_access_allowed_for_cc_jwt(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        created = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_svc_access_app",
            scopes=[service_x.service_name],
        )
        token = await _issue_cc_token(client, created["client_id"], created["client_secret"])

        resp = await client.post(
            ACCESS_URL,
            json={"subject_token": token, "service_name": service_x.service_name},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed"] is True
        assert body["department_id"] == dept_a_with_service.id
        # service_roles пуст для m2m — у клиента нет персональных ролей.
        assert body["service_roles"] == []

    async def test_service_access_denied_after_delete_client(
        self, client, admin_token, dept_a_with_service, service_x
    ):
        created = await _create_m2m_client(
            client, admin_token, dept_a_with_service.id,
            name="cc_svc_after_delete_app",
            scopes=[service_x.service_name],
        )
        token = await _issue_cc_token(client, created["client_id"], created["client_secret"])

        await client.delete(
            f"{CLIENTS_URL}/{created['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = await client.post(
            ACCESS_URL,
            json={"subject_token": token, "service_name": service_x.service_name},
        )
        assert resp.status_code == 200
        assert resp.json()["allowed"] is False
