"""OAuth2 authorization_code flow + PKCE (S256) — кластер I.

Сценарий:

1. Админ заводит сервис `svc_A` / `svc_B`, привязывает к отделу `D`.
2. Юзер `U` в отделе `D` с ролями `[reader]` в `svc_A` и `[reader]` в `svc_B`.
3. Админ создаёт OAuth2-клиента под отделом `D` с `allowed_scopes=[svc_A]`.
4. `U` логинится → получает JWT, идёт на `/oauth2/authorize` с PKCE-challenge
   и `scope=svc_A svc_B` → получает 302 → достаём `code`.
5. `POST /oauth2/token grant_type=authorization_code` с верным `code_verifier`
   → JWT. Introspect показывает, что `allowed_services` обрезан до scope-а
   запроса И до `allowed_scopes` клиента (`{svc_A}`), не отдаёт `svc_B`.
6. Контр-сценарии:
   * битый verifier → 401 INVALID_GRANT;
   * отсутствующий verifier при выданном code с challenge → 401 INVALID_GRANT;
   * повторное использование code → 401 INVALID_GRANT;
   * нерегистрированный redirect_uri на /authorize → 403 REDIRECT_URI_MISMATCH;
   * implicit (response_type=token) → 400 UNSUPPORTED_RESPONSE_TYPE.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration._helpers_I_oauth import (
    OAUTH_AUTHORIZE_URL,
    create_oauth_client,
    ensure_department,
    ensure_service,
    exchange_code,
    grant_service_to_department,
    introspect_token,
    jwt_unverified_payload,
    login_user,
    make_pkce_pair,
    make_user_in_dept,
    request_authorization_code,
    short_id,
)


@pytest.fixture
def authcode_env(auth_client: httpx.Client, admin_token: str):
    """Готовый env: dept + 2 сервиса + юзер с ролями + OAuth-клиент с PKCE-flow."""
    sid = short_id()
    dept_name = f"ac_dept_{sid}"
    svc_a = f"ac_svc_a_{sid}"
    svc_b = f"ac_svc_b_{sid}"
    redirect_uri = f"https://app-{sid}.example.com/cb"

    ensure_service(auth_client, admin_token, svc_a)
    ensure_service(auth_client, admin_token, svc_b)
    dept_id = ensure_department(auth_client, admin_token, dept_name)
    grant_service_to_department(auth_client, admin_token, dept_id, svc_a)
    grant_service_to_department(auth_client, admin_token, dept_id, svc_b)

    user = make_user_in_dept(
        auth_client,
        admin_token,
        department_id=dept_id,
        services_with_roles={svc_a: ["reader"], svc_b: ["reader"]},
    )
    user_login = login_user(auth_client, user["username"], user["_password"])
    assert user_login["status_code"] == 200, user_login
    user_jwt = user_login["body"]["access_token"]

    # `allowed_scopes` клиента ограничивает scope-mask: при /authorize с
    # `scope=svc_a svc_b` сервер обрежет до пересечения с `allowed_scopes`.
    client = create_oauth_client(
        auth_client,
        admin_token,
        department_id=dept_id,
        redirect_uris=[redirect_uri],
        allowed_scopes=[svc_a],
        grant_types=["authorization_code"],
    )
    return {
        "dept_id": dept_id,
        "svc_a": svc_a,
        "svc_b": svc_b,
        "redirect_uri": redirect_uri,
        "user": user,
        "user_jwt": user_jwt,
        "client": client,
    }


class TestAuthCodeWithPKCE:
    def test_full_flow_with_s256_returns_jwt(
        self, auth_client: httpx.Client, authcode_env: dict
    ):
        verifier, challenge = make_pkce_pair()
        code, state_back = request_authorization_code(
            auth_client,
            user_access_token=authcode_env["user_jwt"],
            client_id=authcode_env["client"]["client_id"],
            redirect_uri=authcode_env["redirect_uri"],
            scope=f"{authcode_env['svc_a']} {authcode_env['svc_b']}",
            state="xyz",
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        assert state_back == "xyz"
        assert code

        r = exchange_code(
            auth_client,
            client_id=authcode_env["client"]["client_id"],
            client_secret=authcode_env["client"]["client_secret"],
            code=code,
            redirect_uri=authcode_env["redirect_uri"],
            code_verifier=verifier,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token_type"].lower() == "bearer"

        payload = jwt_unverified_payload(body["access_token"])
        assert payload["sub"] == authcode_env["user"]["id"]
        assert payload.get("actor_type", "user") == "user"
        # `oauth_client_id` и `oauth_scopes` обязаны быть — это позволяет
        # introspect'у сделать scope-intersect и аудиту увидеть, что JWT
        # выписан через OAuth, а не /login.
        assert payload["oauth_client_id"] == authcode_env["client"]["client_id"]
        assert isinstance(payload.get("oauth_scopes"), list)

    def test_scope_intersection_limits_introspect_view(
        self, auth_client: httpx.Client, authcode_env: dict
    ):
        """introspect выписанного JWT возвращает только пересечение
        (granted_scopes) ∩ (live user-permissions). Сервис, отсутствующий
        в granted_scopes (svc_b), не должен светиться в `allowed_services`.
        """
        verifier, challenge = make_pkce_pair()
        # Запрашиваем оба сервиса. Сервер обрежет до allowed_scopes клиента
        # → granted_scopes = [svc_a].
        code, _ = request_authorization_code(
            auth_client,
            user_access_token=authcode_env["user_jwt"],
            client_id=authcode_env["client"]["client_id"],
            redirect_uri=authcode_env["redirect_uri"],
            scope=f"{authcode_env['svc_a']} {authcode_env['svc_b']}",
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        token_resp = exchange_code(
            auth_client,
            client_id=authcode_env["client"]["client_id"],
            client_secret=authcode_env["client"]["client_secret"],
            code=code,
            redirect_uri=authcode_env["redirect_uri"],
            code_verifier=verifier,
        )
        assert token_resp.status_code == 200, token_resp.text
        oauth_jwt = token_resp.json()["access_token"]

        # granted_scopes из ответа /token (RFC 6749 §3.3 — пробельный список).
        granted = set(token_resp.json()["scope"].split())
        assert authcode_env["svc_a"] in granted
        assert authcode_env["svc_b"] not in granted

        ir = introspect_token(auth_client, oauth_jwt)
        assert ir.status_code == 200, ir.text
        ibody = ir.json()
        assert ibody["active"] is True
        assert ibody["subject_type"] == "user"
        # Юзер имеет роли в обоих сервисах, но OAuth-выписка обрезает до svc_a.
        assert authcode_env["svc_a"] in ibody["allowed_services"]
        assert authcode_env["svc_b"] not in ibody["allowed_services"], (
            "OAuth-scope leak: introspect возвращает сервис, который "
            "не был в granted_scopes"
        )
        assert authcode_env["svc_a"] in ibody["service_roles"]
        assert authcode_env["svc_b"] not in ibody["service_roles"]

    def test_wrong_verifier_returns_invalid_grant(
        self, auth_client: httpx.Client, authcode_env: dict
    ):
        verifier, challenge = make_pkce_pair()
        bad_verifier, _ = make_pkce_pair()
        code, _ = request_authorization_code(
            auth_client,
            user_access_token=authcode_env["user_jwt"],
            client_id=authcode_env["client"]["client_id"],
            redirect_uri=authcode_env["redirect_uri"],
            scope=authcode_env["svc_a"],
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        r = exchange_code(
            auth_client,
            client_id=authcode_env["client"]["client_id"],
            client_secret=authcode_env["client"]["client_secret"],
            code=code,
            redirect_uri=authcode_env["redirect_uri"],
            code_verifier=bad_verifier,
        )
        assert r.status_code == 401, r.text
        assert r.json()["error_code"] == "INVALID_GRANT"

    def test_missing_verifier_when_challenge_set_returns_invalid_grant(
        self, auth_client: httpx.Client, authcode_env: dict
    ):
        _, challenge = make_pkce_pair()
        code, _ = request_authorization_code(
            auth_client,
            user_access_token=authcode_env["user_jwt"],
            client_id=authcode_env["client"]["client_id"],
            redirect_uri=authcode_env["redirect_uri"],
            scope=authcode_env["svc_a"],
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        r = exchange_code(
            auth_client,
            client_id=authcode_env["client"]["client_id"],
            client_secret=authcode_env["client"]["client_secret"],
            code=code,
            redirect_uri=authcode_env["redirect_uri"],
            code_verifier=None,
        )
        assert r.status_code == 401, r.text
        assert r.json()["error_code"] == "INVALID_GRANT"

    def test_code_is_single_use(
        self, auth_client: httpx.Client, authcode_env: dict
    ):
        verifier, challenge = make_pkce_pair()
        code, _ = request_authorization_code(
            auth_client,
            user_access_token=authcode_env["user_jwt"],
            client_id=authcode_env["client"]["client_id"],
            redirect_uri=authcode_env["redirect_uri"],
            scope=authcode_env["svc_a"],
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        first = exchange_code(
            auth_client,
            client_id=authcode_env["client"]["client_id"],
            client_secret=authcode_env["client"]["client_secret"],
            code=code,
            redirect_uri=authcode_env["redirect_uri"],
            code_verifier=verifier,
        )
        assert first.status_code == 200, first.text

        # Второй обмен того же `code` обязан упасть. CAS-mark_used уже
        # консумнул его; reuse-detector ловит и для legit-клиента, и для
        # перехвата.
        second = exchange_code(
            auth_client,
            client_id=authcode_env["client"]["client_id"],
            client_secret=authcode_env["client"]["client_secret"],
            code=code,
            redirect_uri=authcode_env["redirect_uri"],
            code_verifier=verifier,
        )
        assert second.status_code == 401, second.text
        # Точное error_code — `OAUTH_CODE_INVALID` или `INVALID_GRANT`
        # в зависимости от того, в какой фазе фолбэк сработал. Любой
        # из них допустим — главное не 200.
        assert second.json()["error_code"] in {"OAUTH_CODE_INVALID", "INVALID_GRANT"}

    def test_unregistered_redirect_uri_rejected_on_authorize(
        self, auth_client: httpx.Client, authcode_env: dict
    ):
        # `/authorize` валидирует, что redirect_uri в whitelist'е клиента.
        _, challenge = make_pkce_pair()
        params = {
            "client_id": authcode_env["client"]["client_id"],
            "redirect_uri": "https://evil.example.com/cb",
            "scope": authcode_env["svc_a"],
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        r = auth_client.get(
            OAUTH_AUTHORIZE_URL,
            params=params,
            headers={"Authorization": f"Bearer {authcode_env['user_jwt']}"},
            follow_redirects=False,
        )
        assert r.status_code in (400, 403), r.text
        # Зашитый error_code на стороне oauth_service.
        assert r.json()["error_code"] == "REDIRECT_URI_MISMATCH"

    def test_implicit_response_type_rejected(
        self, auth_client: httpx.Client, authcode_env: dict
    ):
        r = auth_client.get(
            OAUTH_AUTHORIZE_URL,
            params={
                "client_id": authcode_env["client"]["client_id"],
                "redirect_uri": authcode_env["redirect_uri"],
                "scope": authcode_env["svc_a"],
                "response_type": "token",
            },
            headers={"Authorization": f"Bearer {authcode_env['user_jwt']}"},
            follow_redirects=False,
        )
        assert r.status_code == 400, r.text
        assert r.json()["error_code"] == "UNSUPPORTED_RESPONSE_TYPE"
