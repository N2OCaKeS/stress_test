"""OAuth2 client_credentials grant — m2m JWT с минимизированным payload.

Контракт (см. `auth_service/src/services/oauth_service.py::client_credentials_token`):

* JWT-payload содержит ровно: `sub = client.client_id`, `actor_type = oauth_client`,
  `oauth_client_id`. Никаких `department_id`, `allowed_services`,
  `service_roles`, `username`, `platform_role`, `oauth_scopes` —
  base64url-decode payload'а без ключа НЕ должен раскрывать привязку клиента
  к отделу и список сервисов.
* `introspect` пересчитывает `allowed_services` как INTERSECT
  `(dept_services ∩ client.allowed_scopes)` через `_introspect_oauth_client_jwt`.
* Revoke клиента (DELETE /oauth2/clients/{id}) → следующий introspect
  возвращает `active=false`.
* Grant не разрешён, если в `client.grant_types` нет `client_credentials`.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration._helpers_I_oauth import (
    OAUTH_CLIENTS_URL,
    client_credentials_token,
    create_oauth_client,
    ensure_department,
    ensure_service,
    grant_service_to_department,
    introspect_token,
    jwt_unverified_payload,
    short_id,
)


@pytest.fixture
def cc_env(auth_client: httpx.Client, admin_token: str):
    sid = short_id()
    dept_name = f"cc_dept_{sid}"
    svc_a = f"cc_svc_a_{sid}"
    svc_b = f"cc_svc_b_{sid}"

    ensure_service(auth_client, admin_token, svc_a)
    ensure_service(auth_client, admin_token, svc_b)
    dept_id = ensure_department(auth_client, admin_token, dept_name)
    grant_service_to_department(auth_client, admin_token, dept_id, svc_a)
    grant_service_to_department(auth_client, admin_token, dept_id, svc_b)

    # `allowed_scopes` < dept_services — проверим, что introspect возвращает
    # пересечение (svc_a), а не объединение (svc_a, svc_b).
    client = create_oauth_client(
        auth_client,
        admin_token,
        department_id=dept_id,
        allowed_scopes=[svc_a],
        grant_types=["client_credentials"],
    )
    return {
        "dept_id": dept_id,
        "svc_a": svc_a,
        "svc_b": svc_b,
        "client": client,
    }


class TestClientCredentials:
    def test_token_issued_with_minimized_payload(
        self, auth_client: httpx.Client, cc_env: dict
    ):
        r = client_credentials_token(
            auth_client,
            client_id=cc_env["client"]["client_id"],
            client_secret=cc_env["client"]["client_secret"],
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token_type"].lower() == "bearer"
        assert body["expires_in"] > 0

        payload = jwt_unverified_payload(body["access_token"])
        # ── Что ОБЯЗАНО быть ────────────────────────────────────────────────
        assert payload["sub"] == cc_env["client"]["client_id"], (
            "sub для client_credentials JWT — это публичный client_id, не user_id"
        )
        assert payload["actor_type"] == "oauth_client"
        assert payload["oauth_client_id"] == cc_env["client"]["client_id"]

        # ── Чего быть НЕ должно (минимизация) ───────────────────────────────
        for forbidden in (
            "department_id",
            "department_name",
            "allowed_services",
            "service_roles",
            "platform_role",
            "username",
            "oauth_scopes",
        ):
            assert forbidden not in payload, (
                f"client_credentials JWT leaks {forbidden}: payload={payload}"
            )

    def test_introspect_rebuilds_allowed_services_via_intersect(
        self, auth_client: httpx.Client, cc_env: dict
    ):
        token_resp = client_credentials_token(
            auth_client,
            client_id=cc_env["client"]["client_id"],
            client_secret=cc_env["client"]["client_secret"],
        )
        access_token = token_resp.json()["access_token"]

        ir = introspect_token(auth_client, access_token)
        assert ir.status_code == 200, ir.text
        ibody = ir.json()
        assert ibody["active"] is True
        assert ibody["subject_type"] == "oauth_client"
        assert ibody["sub"] == cc_env["client"]["client_id"]
        # INTERSECT(dept_services={svc_a, svc_b}, allowed_scopes={svc_a}) = {svc_a}.
        assert ibody["allowed_services"] == [cc_env["svc_a"]]
        # service_roles у oauth_client всегда пустой — клиент не носитель ролей.
        assert ibody["service_roles"] == {}
        assert ibody["is_banned"] is False
        assert ibody["department_id"] == cc_env["dept_id"]

    def test_bad_secret_returns_401(
        self, auth_client: httpx.Client, cc_env: dict
    ):
        r = client_credentials_token(
            auth_client,
            client_id=cc_env["client"]["client_id"],
            client_secret="wrong-secret",
        )
        assert r.status_code == 401, r.text
        assert r.json()["error_code"] == "OAUTH_CLIENT_INVALID"

    def test_unknown_client_returns_401(
        self, auth_client: httpx.Client
    ):
        r = client_credentials_token(
            auth_client,
            client_id=f"cli_ghost_{short_id()}",
            client_secret="anything",
        )
        assert r.status_code == 401, r.text
        assert r.json()["error_code"] == "OAUTH_CLIENT_INVALID"

    def test_grant_not_in_client_grant_types_rejected(
        self, auth_client: httpx.Client, admin_token: str
    ):
        sid = short_id()
        dept_id = ensure_department(auth_client, admin_token, f"cc_only_ac_{sid}")
        client = create_oauth_client(
            auth_client,
            admin_token,
            department_id=dept_id,
            grant_types=["authorization_code"],  # без client_credentials
        )
        r = client_credentials_token(
            auth_client,
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        )
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "GRANT_TYPE_NOT_ALLOWED"

    def test_revoked_client_introspect_returns_inactive(
        self, auth_client: httpx.Client, admin_token: str, cc_env: dict
    ):
        token_resp = client_credentials_token(
            auth_client,
            client_id=cc_env["client"]["client_id"],
            client_secret=cc_env["client"]["client_secret"],
        )
        access_token = token_resp.json()["access_token"]

        # До revoke — active.
        before = introspect_token(auth_client, access_token).json()
        assert before["active"] is True

        # DELETE клиента — soft-delete (`is_active=False`).
        client_db_id = cc_env["client"]["id"]
        rd = auth_client.delete(
            f"{OAUTH_CLIENTS_URL}/{client_db_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert rd.status_code == 200, rd.text

        # Тот же подписанный JWT — теперь introspect возвращает active=false,
        # потому что revalidate через БД ловит `is_active=False`.
        after = introspect_token(auth_client, access_token).json()
        assert after["active"] is False, (
            "OAuth client revoke must take effect on introspect immediately"
        )

    def test_token_response_scope_lists_granted(
        self, auth_client: httpx.Client, cc_env: dict
    ):
        """`scope` в ответе /token совпадает с granted (INTERSECT)."""
        r = client_credentials_token(
            auth_client,
            client_id=cc_env["client"]["client_id"],
            client_secret=cc_env["client"]["client_secret"],
        )
        granted = set(r.json()["scope"].split())
        assert granted == {cc_env["svc_a"]}
