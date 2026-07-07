"""Тесты: OAuth2 refresh_token grant (RFC 6749 §6) с ротацией и reuse-detection.

Зеркалит модель безопасности user-сессионного refresh:
* refresh выдаётся на authorization_code-обмене только клиентам с grant
  `refresh_token`;
* `grant_type=refresh_token` ротирует пару (новый access + новый refresh,
  старый refresh инвалидируется);
* предъявление старого refresh после ротации → kill-switch по всей цепочке
  (client_id, user_id);
* approved scope'ы сохраняются в новом access;
* expired refresh / чужой клиент / клиент без grant — отбиваются.
"""

from datetime import timedelta

from src.core.security import hash_opaque_token
from src.repositories.oauth_refresh_tokens import OAuthRefreshTokenRepository
from src.utils.time import utcnow

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _create_client(
    http_client,
    admin_token,
    dept_id,
    *,
    name,
    grant_types,
    redirect_uri="https://app.example.com/callback",
    scopes=None,
    is_public=False,
):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": grant_types,
            "redirect_uris": [redirect_uri],
            "allowed_scopes": scopes or [],
            "is_public": is_public,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _authorize_code(
    http_client,
    user_token,
    oauth_client,
    *,
    redirect_uri="https://app.example.com/callback",
    scope="",
):
    params = {
        "client_id": oauth_client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }
    if scope:
        params["scope"] = scope
    resp = await http_client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_token}"},
        params=params,
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    return resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]


async def _exchange_code(http_client, oauth_client, code, redirect_uri="https://app.example.com/callback"):
    return await http_client.post(
        TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": oauth_client["client_id"],
            "client_secret": oauth_client["client_secret"],
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )


async def _refresh(http_client, oauth_client, refresh_token):
    return await http_client.post(
        TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "client_id": oauth_client["client_id"],
            "client_secret": oauth_client["client_secret"],
            "refresh_token": refresh_token,
        },
    )


async def _full_authcode_with_refresh(http_client, admin_token, user_token, dept_id, *, name, scope=""):
    """Создать client с refresh-grant, пройти authorize+exchange, вернуть (client, token-json)."""
    oauth_client = await _create_client(
        http_client, admin_token, dept_id,
        name=name,
        grant_types=["authorization_code", "refresh_token"],
        scopes=[s for s in scope.split() if s] or None,
    )
    code = await _authorize_code(http_client, user_token, oauth_client, scope=scope)
    resp = await _exchange_code(http_client, oauth_client, code)
    assert resp.status_code == 200, resp.text
    return oauth_client, resp.json()


# ── 1. Issuance на authorization_code обмене ──────────────────────────────────


class TestRefreshIssuance:
    async def test_refresh_issued_when_grant_present(self, client, admin_token, user_a_token, dept_a):
        _, tok = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a.id, name="rt_issue_app",
        )
        assert tok.get("refresh_token")
        assert tok["refresh_token"].startswith("dbos_oauth_rt_")

    async def test_no_refresh_when_grant_absent(self, client, admin_token, user_a_token, dept_a):
        oauth_client = await _create_client(
            client, admin_token, dept_a.id,
            name="rt_no_grant_app",
            grant_types=["authorization_code"],
        )
        code = await _authorize_code(client, user_a_token, oauth_client)
        resp = await _exchange_code(client, oauth_client, code)
        assert resp.status_code == 200, resp.text
        assert resp.json().get("refresh_token") is None


# ── 2. Ротация ────────────────────────────────────────────────────────────────


class TestRefreshRotation:
    async def test_refresh_returns_new_pair(self, client, admin_token, user_a_token, dept_a):
        oauth_client, tok = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a.id, name="rt_rotate_app",
        )
        old_refresh = tok["refresh_token"]
        resp = await _refresh(client, oauth_client, old_refresh)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["refresh_token"] != old_refresh

    async def test_old_refresh_rejected_after_rotation(self, client, admin_token, user_a_token, dept_a):
        oauth_client, tok = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a.id, name="rt_old_rejected_app",
        )
        old_refresh = tok["refresh_token"]
        first = await _refresh(client, oauth_client, old_refresh)
        assert first.status_code == 200, first.text
        new_refresh = first.json()["refresh_token"]
        # Немедленный повтор старого (непосредственно-предыдущего) refresh —
        # benign-гонка в grace-окне: 401 RACE, но БЕЗ kill-switch по цепочке.
        again = await _refresh(client, oauth_client, old_refresh)
        assert again.status_code == 401, again.text
        assert again.json()["error_code"] == "REFRESH_TOKEN_RACE"
        # Цепочка жива — свежий refresh победителя гонки всё ещё работает.
        ok = await _refresh(client, oauth_client, new_refresh)
        assert ok.status_code == 200, ok.text

    async def test_scope_preserved_across_refresh(
        self, client, admin_token, user_a_token, dept_a_with_service, service_x,
    ):
        scope = service_x.service_name
        oauth_client, tok = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a_with_service.id,
            name="rt_scope_app", scope=scope,
        )
        assert tok["scope"] == scope
        resp = await _refresh(client, oauth_client, tok["refresh_token"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["scope"] == scope


# ── 3. Reuse-detection → chain invalidation ───────────────────────────────────


class TestRefreshReuseDetection:
    async def test_reuse_kills_chain(self, client, admin_token, user_a_token, dept_a):
        oauth_client, tok = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a.id, name="rt_reuse_app",
        )
        r1 = await _refresh(client, oauth_client, tok["refresh_token"])
        assert r1.status_code == 200, r1.text
        # Вторая ротация — исходный refresh уходит с позиции непосредственно-
        # предыдущего, чтобы его повтор ловился как reuse, а не grace-гонка.
        r2 = await _refresh(client, oauth_client, r1.json()["refresh_token"])
        assert r2.status_code == 200, r2.text
        newer_refresh = r2.json()["refresh_token"]

        # Reuse старого (не последнего) refresh — kill-switch.
        reuse = await _refresh(client, oauth_client, tok["refresh_token"])
        assert reuse.status_code == 401, reuse.text
        assert reuse.json()["error_code"] == "REFRESH_TOKEN_INVALID"

        # После kill-switch даже «честный» новый refresh из цепочки revoked.
        after = await _refresh(client, oauth_client, newer_refresh)
        assert after.status_code == 401, after.text
        assert after.json()["error_code"] == "REFRESH_TOKEN_INVALID"


# ── 4. Ошибки ─────────────────────────────────────────────────────────────────


class TestRefreshErrors:
    async def test_unknown_refresh_token(self, client, admin_token, user_a_token, dept_a):
        oauth_client, _ = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a.id, name="rt_unknown_app",
        )
        resp = await _refresh(client, oauth_client, "dbos_oauth_rt_not-a-real-token")
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "REFRESH_TOKEN_INVALID"

    async def test_grant_not_enabled(self, client, admin_token, user_a_token, dept_a):
        # Клиент без refresh_token grant — даже с валидно выглядящим телом 403.
        oauth_client = await _create_client(
            client, admin_token, dept_a.id,
            name="rt_grant_off_app",
            grant_types=["authorization_code"],
        )
        resp = await _refresh(client, oauth_client, "dbos_oauth_rt_whatever")
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "GRANT_TYPE_NOT_ALLOWED"

    async def test_wrong_client_cannot_rotate(self, client, admin_token, user_a_token, dept_a):
        oauth_client, tok = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a.id, name="rt_owner_app",
        )
        other = await _create_client(
            client, admin_token, dept_a.id,
            name="rt_attacker_app",
            grant_types=["authorization_code", "refresh_token"],
        )
        # Предъявляем чужой refresh от имени другого клиента.
        resp = await _refresh(client, other, tok["refresh_token"])
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "REFRESH_TOKEN_INVALID"

    async def test_bad_client_secret_rejected(self, client, admin_token, user_a_token, dept_a):
        oauth_client, tok = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a.id, name="rt_badsecret_app",
        )
        resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": oauth_client["client_id"],
                "client_secret": "cs_totally-wrong-secret",
                "refresh_token": tok["refresh_token"],
            },
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "OAUTH_CLIENT_INVALID"

    async def test_expired_refresh_rejected(self, client, admin_token, user_a_token, dept_a, db):
        oauth_client, tok = await _full_authcode_with_refresh(
            client, admin_token, user_a_token, dept_a.id, name="rt_expired_app",
        )
        # Бэкдейтим expires_at напрямую в БД — TTL по умолчанию дни, через HTTP
        # не достать.
        repo = OAuthRefreshTokenRepository(db)
        stored = await repo.get_active_by_token_hash(hash_opaque_token(tok["refresh_token"]))
        assert stored is not None
        stored.expires_at = utcnow() - timedelta(seconds=1)
        await db.flush()
        resp = await _refresh(client, oauth_client, tok["refresh_token"])
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "REFRESH_TOKEN_EXPIRED"
