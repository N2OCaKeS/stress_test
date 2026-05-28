"""`POST /authorization/introspect` под все типы токенов — кластер I.

Что покрываем:

* user JWT — revalidate из БД: после ban → `active=false` (не ждём истечения
  JWT); после смены департамента / отзыва service-роли — следующий introspect
  видит actual permission-mask. Кеша нет, revoke мгновенный.
* PAT — group-сервисы видны через `collect_user_permissions` (с учётом
  group-derived service-access; асимметрию с issue-time `create_pat` ловят
  отдельные suite-тесты).
* bot-token — `actor_type=bot`, права через `collect_bot_permissions`.
* oauth_client JWT (cc-минимизированный) — см. `test_e2e_I_client_credentials.py`.
* expired/revoked токены — `active=false`.
* мусор / пустой токен — `active=false`.
"""

from __future__ import annotations

import time

import httpx

from tests.integration._helpers_I_oauth import (
    BOTS_URL,
    USERS_URL,
    ban_user,
    ensure_department,
    ensure_service,
    grant_service_to_department,
    introspect_token,
    issue_pat,
    login_user,
    make_user_in_dept,
    short_id,
)


def _create_bot_with_token(
    auth_client: httpx.Client,
    admin_token: str,
    *,
    department_id: str,
    allowed_services: list[str],
    service_roles: list[dict] | None = None,
) -> tuple[dict, str, str]:
    """Создать бота + выписать ему один bot-token. Вернуть (bot, token_id, token_plaintext)."""
    bot_resp = auth_client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": f"bot_{short_id()}",
            "department_id": department_id,
            "allowed_services": allowed_services,
            "service_roles": service_roles or [],
        },
    )
    assert bot_resp.status_code in (200, 201), bot_resp.text
    bot = bot_resp.json()
    bot_id = bot.get("bot_id") or bot.get("id")

    tok_resp = auth_client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": f"t_{short_id()}"},
    )
    assert tok_resp.status_code == 201, tok_resp.text
    tok_body = tok_resp.json()
    plain = tok_body.get("token") or tok_body.get("plaintext")
    token_id = tok_body.get("id") or tok_body.get("bot_token_id") or tok_body.get("token_id")
    return bot, token_id, plain


class TestIntrospectUserJWT:
    def test_active_user_jwt_returns_full_identity(
        self, auth_client: httpx.Client, admin_token: str
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"is_user_{sid}")
        svc = f"is_svc_{sid}"
        ensure_service(auth_client, admin_token, svc)
        grant_service_to_department(auth_client, admin_token, dept, svc)
        user = make_user_in_dept(
            auth_client, admin_token, department_id=dept,
            services_with_roles={svc: ["reader"]},
        )
        token = login_user(auth_client, user["username"], user["_password"])["body"]["access_token"]

        ir = introspect_token(auth_client, token).json()
        assert ir["active"] is True
        assert ir["subject_type"] == "user"
        assert ir["sub"] == user["id"]
        assert ir["username"] == user["username"]
        assert ir["department_id"] == dept
        assert svc in ir["allowed_services"]
        assert "reader" in ir["service_roles"].get(svc, [])

    def test_ban_invalidates_jwt_introspect_immediately(
        self, auth_client: httpx.Client, admin_token: str
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"is_ban_{sid}")
        user = make_user_in_dept(auth_client, admin_token, department_id=dept)
        token = login_user(auth_client, user["username"], user["_password"])["body"]["access_token"]

        # До ban — active.
        before = introspect_token(auth_client, token).json()
        assert before["active"] is True

        ban_user(auth_client, admin_token, user["id"])
        # Без кеша — следующий вызов сразу видит ban.
        after = introspect_token(auth_client, token).json()
        assert after["active"] is False, "ban must revoke JWT immediately, no TTL grace"

    def test_role_revoke_reflected_in_next_introspect(
        self, auth_client: httpx.Client, admin_token: str
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"is_role_{sid}")
        svc = f"is_role_svc_{sid}"
        ensure_service(auth_client, admin_token, svc)
        grant_service_to_department(auth_client, admin_token, dept, svc)
        user = make_user_in_dept(
            auth_client, admin_token, department_id=dept,
            services_with_roles={svc: ["reader"]},
        )
        token = login_user(auth_client, user["username"], user["_password"])["body"]["access_token"]

        before = introspect_token(auth_client, token).json()
        assert "reader" in before["service_roles"].get(svc, [])

        # Снятие всех ролей: replace на пустой список.
        rr = auth_client.post(
            f"{USERS_URL}/{user['id']}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": svc, "roles": []},
        )
        assert rr.status_code in (200, 201), rr.text

        after = introspect_token(auth_client, token).json()
        assert after["active"] is True
        assert svc not in after["service_roles"] or after["service_roles"][svc] == [], (
            "role revoke must be visible to introspect immediately"
        )


class TestIntrospectPAT:
    def test_pat_introspect_returns_user_sub_and_subject_type(
        self,
        auth_client: httpx.Client,
        admin_token: str,
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"is_pat_{sid}")
        svc = f"is_pat_svc_{sid}"
        ensure_service(auth_client, admin_token, svc)
        grant_service_to_department(auth_client, admin_token, dept, svc)
        user = make_user_in_dept(
            auth_client, admin_token, department_id=dept,
            services_with_roles={svc: ["operator"]},
        )
        u_jwt = login_user(auth_client, user["username"], user["_password"])["body"]["access_token"]

        pat_body = issue_pat(
            auth_client, u_jwt, name=f"pat_intro_{sid}", allowed_services=[svc]
        )
        plain_pat = pat_body["token"]
        assert plain_pat.startswith("dbos_pat_")

        ir = introspect_token(auth_client, plain_pat).json()
        assert ir["active"] is True
        # subject_type для PAT — `user` (PAT — токен от лица юзера, не отдельный actor).
        assert ir["subject_type"] == "user"
        assert ir["sub"] == user["id"]
        assert ir["username"] == user["username"]
        assert svc in ir["allowed_services"]
        assert "operator" in ir["service_roles"].get(svc, [])

    def test_revoked_pat_returns_inactive_within_one_second(
        self,
        auth_client: httpx.Client,
        admin_token: str,
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"is_pat_rev_{sid}")
        user = make_user_in_dept(auth_client, admin_token, department_id=dept)
        u_jwt = login_user(auth_client, user["username"], user["_password"])["body"]["access_token"]

        pat_body = issue_pat(auth_client, u_jwt, name=f"pat_rev_{sid}")
        plain_pat = pat_body["token"]
        target_id = pat_body["token_id"]

        # introspect → 200 / active.
        first = introspect_token(auth_client, plain_pat).json()
        assert first["active"] is True

        # Revoke.
        t0 = time.monotonic()
        dr = auth_client.delete(
            f"/api/auth/v1/tokens/{target_id}",
            headers={"Authorization": f"Bearer {u_jwt}"},
        )
        assert dr.status_code == 200, dr.text

        # Следующий introspect — active=false в < 1s.
        ir2 = introspect_token(auth_client, plain_pat).json()
        elapsed = time.monotonic() - t0
        assert ir2["active"] is False
        assert elapsed < 1.0, f"PAT revoke propagation too slow: {elapsed:.3f}s"


class TestIntrospectBotToken:
    def test_bot_token_introspect_returns_bot_subject_type(
        self, auth_client: httpx.Client, admin_token: str
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"is_bot_{sid}")
        svc = f"is_bot_svc_{sid}"
        ensure_service(auth_client, admin_token, svc)
        grant_service_to_department(auth_client, admin_token, dept, svc)

        bot, _tok_id, plain = _create_bot_with_token(
            auth_client, admin_token,
            department_id=dept,
            allowed_services=[svc],
            service_roles=[{"service_name": svc, "roles": ["reader"]}],
        )
        assert plain.startswith("dbos_bot_")

        ir = introspect_token(auth_client, plain).json()
        assert ir["active"] is True
        assert ir["subject_type"] == "bot"
        assert ir["sub"] == (bot.get("bot_id") or bot.get("id"))
        assert ir["department_id"] == dept
        assert svc in ir["allowed_services"]
        assert "reader" in ir["service_roles"].get(svc, [])

    def test_revoked_bot_token_returns_inactive(
        self, auth_client: httpx.Client, admin_token: str
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"is_botrev_{sid}")
        svc = f"is_botrev_svc_{sid}"
        ensure_service(auth_client, admin_token, svc)
        grant_service_to_department(auth_client, admin_token, dept, svc)

        bot, tok_id, plain = _create_bot_with_token(
            auth_client, admin_token,
            department_id=dept,
            allowed_services=[svc],
        )
        before = introspect_token(auth_client, plain).json()
        assert before["active"] is True

        bot_id = bot.get("bot_id") or bot.get("id")
        dr = auth_client.delete(
            f"{BOTS_URL}/{bot_id}/tokens/{tok_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert dr.status_code == 200, dr.text

        after = introspect_token(auth_client, plain).json()
        assert after["active"] is False


class TestIntrospectInvalidTokens:
    def test_garbage_token_returns_inactive(self, auth_client: httpx.Client):
        ir = introspect_token(auth_client, "totally-not-a-token").json()
        assert ir["active"] is False

    def test_malformed_jwt_returns_inactive(self, auth_client: httpx.Client):
        ir = introspect_token(auth_client, "aaa.bbb.ccc").json()
        assert ir["active"] is False

    def test_empty_token_returns_inactive(self, auth_client: httpx.Client):
        # Пустая строка — нет такого PAT/bot/JWT.
        ir = introspect_token(auth_client, "").json()
        assert ir["active"] is False

    def test_introspect_requires_service_api_key(self, auth_client: httpx.Client):
        # Без Bearer SERVICE_API_KEY — 401. Защита от brute-force ворованных
        # PAT через публичный endpoint.
        r = auth_client.post(
            "/api/auth/v1/authorization/introspect",
            json={"token": "anything"},
        )
        assert r.status_code == 401
