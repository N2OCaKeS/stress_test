"""OAuth2 password flow (`POST /token` form-encoded) — кластер I.

`POST /api/auth/v1/token` принимает `application/x-www-form-urlencoded` с
полями `grant_type=password`, `username`, `password`. Под капотом — тот же
`auth_service.login`, что и `/login`, со всеми guard'ами (lockout, ban,
USER_BANNED).

Что покрываем:

* happy path — креды валидны → 200 + JWT с `actor_type=user` в payload;
* bad creds → 401 INVALID_CREDENTIALS;
* permanent-ban → 403 USER_BANNED (логин возвращает 403, не 401, как
  отдельная семантика «ты заблокирован», а не «пароль не подошёл»).
"""

from __future__ import annotations

import httpx

from tests.integration._helpers_I_oauth import (
    LOGIN_JSON_URL,
    TOKEN_FORM_URL,
    ban_user,
    jwt_unverified_payload,
    make_user_in_dept,
    ensure_department,
    short_id,
)


class TestOAuthPasswordFlow:
    def test_password_grant_form_returns_jwt(
        self, auth_client: httpx.Client, admin_token: str
    ):
        dept = ensure_department(auth_client, admin_token, f"oauth_pw_{short_id()}")
        user = make_user_in_dept(auth_client, admin_token, department_id=dept)

        r = auth_client.post(
            TOKEN_FORM_URL,
            data={
                "grant_type": "password",
                "username": user["username"],
                "password": user["_password"],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "access_token" in body
        assert body["token_type"].lower() == "bearer"

        payload = jwt_unverified_payload(body["access_token"])
        # JWT-payload минимальный: sub + actor_type + стандартные клеймы.
        assert payload["sub"] == user["id"]
        assert payload.get("actor_type", "user") == "user"
        # Чувствительных claim'ов в payload не должно быть — их выдаёт introspect.
        for forbidden in ("allowed_services", "service_roles", "platform_role", "department_id"):
            assert forbidden not in payload, f"JWT payload leaks {forbidden}"

    def test_password_grant_without_grant_type_still_works(
        self, auth_client: httpx.Client, admin_token: str
    ):
        # `OAuth2PasswordRequestForm` помечает `grant_type` как опциональный,
        # но `OAuth2PasswordRequestFormStrict` его требует. Текущий код у нас
        # использует permissive-вариант — фиксируем это поведение.
        dept = ensure_department(auth_client, admin_token, f"oauth_pw_loose_{short_id()}")
        user = make_user_in_dept(auth_client, admin_token, department_id=dept)
        r = auth_client.post(
            TOKEN_FORM_URL,
            data={"username": user["username"], "password": user["_password"]},
        )
        assert r.status_code == 200, r.text

    def test_password_grant_bad_credentials_returns_401(
        self, auth_client: httpx.Client, admin_token: str
    ):
        dept = ensure_department(auth_client, admin_token, f"oauth_pw_bad_{short_id()}")
        user = make_user_in_dept(auth_client, admin_token, department_id=dept)

        r = auth_client.post(
            TOKEN_FORM_URL,
            data={
                "grant_type": "password",
                "username": user["username"],
                "password": "wrong-password",
            },
        )
        assert r.status_code == 401, r.text
        assert r.json()["error_code"] == "INVALID_CREDENTIALS"

    def test_password_grant_unknown_user_returns_401(
        self, auth_client: httpx.Client
    ):
        r = auth_client.post(
            TOKEN_FORM_URL,
            data={
                "grant_type": "password",
                "username": f"ghost_{short_id()}",
                "password": "irrelevant",
            },
        )
        assert r.status_code == 401, r.text
        assert r.json()["error_code"] == "INVALID_CREDENTIALS"

    def test_password_grant_banned_user_returns_403(
        self, auth_client: httpx.Client, admin_token: str
    ):
        dept = ensure_department(auth_client, admin_token, f"oauth_pw_ban_{short_id()}")
        user = make_user_in_dept(auth_client, admin_token, department_id=dept)

        ban_user(auth_client, admin_token, user["id"])

        r = auth_client.post(
            TOKEN_FORM_URL,
            data={
                "grant_type": "password",
                "username": user["username"],
                "password": user["_password"],
            },
        )
        # `auth_service.login` поднимает `USER_BANNED` как AuthorizationError →
        # маппится в 403. Если в будущем регрессирует до 401 — тест поймает.
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "USER_BANNED"

    def test_password_grant_equivalent_to_login_json(
        self, auth_client: httpx.Client, admin_token: str
    ):
        """`POST /token` form == `POST /login` json — одна и та же выдача."""
        dept = ensure_department(auth_client, admin_token, f"oauth_pw_eq_{short_id()}")
        user = make_user_in_dept(auth_client, admin_token, department_id=dept)

        json_r = auth_client.post(
            LOGIN_JSON_URL,
            json={"username": user["username"], "password": user["_password"]},
        )
        form_r = auth_client.post(
            TOKEN_FORM_URL,
            data={
                "grant_type": "password",
                "username": user["username"],
                "password": user["_password"],
            },
        )
        assert json_r.status_code == 200 and form_r.status_code == 200

        json_payload = jwt_unverified_payload(json_r.json()["access_token"])
        form_payload = jwt_unverified_payload(form_r.json()["access_token"])
        # `sub` совпадает; `exp/iat` могут отличаться на доли секунды.
        assert json_payload["sub"] == form_payload["sub"]
        assert json_payload.get("actor_type", "user") == form_payload.get("actor_type", "user")
