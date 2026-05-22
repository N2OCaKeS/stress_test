"""Edge cases для Docker registry endpoints.

Базовые happy лежат в test_docker_config.py / test_docker_token.py.
Здесь только пропущенные ветви из TEST_COVERAGE:

* `PATCH /registry/{dept_id}` — частичное обновление (один ключ), конфликт
  обновления несуществующего конфига, неверный enum.
* `GET /registry/{dept_id}` — несуществующий dept_id → 404.
* `GET /docker/token` Basic auth с битым base64 / без префикса.
"""

import base64

CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
TOKEN_URL = "/api/auth/v1/docker/token"


def _basic_auth(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


# ── PATCH /registry/{dept_id} ────────────────────────────────────────────────

class TestPatchRegistry:
    async def test_partial_update_only_pull_policy(self, client, admin_token, dept_a, docker_registry_enabled):
        resp = await client.patch(
            CONFIG_URL.format(dept_id=dept_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"pull_policy": "all"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pull_policy"] == "all"

    async def test_partial_update_only_is_enabled(self, client, admin_token, dept_a, docker_registry_enabled):
        resp = await client.patch(
            CONFIG_URL.format(dept_id=dept_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_enabled"] is False

    async def test_patch_invalid_pull_policy_returns_422(self, client, admin_token, dept_a, docker_registry_enabled):
        resp = await client.patch(
            CONFIG_URL.format(dept_id=dept_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"pull_policy": "unknown_policy"},
        )
        assert resp.status_code == 422

    async def test_patch_nonexistent_config_returns_404(self, client, admin_token, dept_b):
        """dept_b не имеет docker config — PATCH должен дать 404, не создать втихую."""
        resp = await client.patch(
            CONFIG_URL.format(dept_id=dept_b.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"pull_policy": "all"},
        )
        assert resp.status_code == 404

    async def test_regular_user_cannot_patch(self, client, user_a_token, dept_a, docker_registry_enabled):
        resp = await client.patch(
            CONFIG_URL.format(dept_id=dept_a.id),
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"pull_policy": "all"},
        )
        assert resp.status_code == 403


# ── GET /registry/{dept_id} edge ─────────────────────────────────────────────

class TestGetRegistryEdge:
    async def test_get_nonexistent_returns_404(self, client, admin_token, dept_b):
        resp = await client.get(
            CONFIG_URL.format(dept_id=dept_b.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    async def test_get_for_phantom_department_returns_404(self, client, admin_token):
        resp = await client.get(
            CONFIG_URL.format(dept_id="dep_phantom"),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404


# ── /docker/token Basic auth parsing ─────────────────────────────────────────

class TestDockerTokenBasicAuth:
    async def test_missing_authorization_header_returns_401(self, client, docker_registry_enabled):
        resp = await client.get(TOKEN_URL, params={"service": "registry"})
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "MISSING_CREDENTIALS"

    async def test_bearer_instead_of_basic_returns_401(self, client, docker_registry_enabled):
        resp = await client.get(
            TOKEN_URL,
            params={"service": "registry"},
            headers={"Authorization": "Bearer some.jwt.token"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "MISSING_CREDENTIALS"

    async def test_malformed_base64_returns_401(self, client, docker_registry_enabled):
        resp = await client.get(
            TOKEN_URL,
            params={"service": "registry"},
            headers={"Authorization": "Basic !!!not-base64!!!"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

    async def test_basic_without_colon_is_treated_as_no_password(self, client, docker_registry_enabled):
        """`Basic <b64('user')>` без двоеточия — пустой пароль, аутентификация падает."""
        encoded = base64.b64encode(b"only_username_no_colon").decode("ascii")
        resp = await client.get(
            TOKEN_URL,
            params={"service": "registry"},
            headers={"Authorization": f"Basic {encoded}"},
        )
        assert resp.status_code == 401

    async def test_wrong_password_returns_401(self, client, docker_registry_enabled, user_a):
        resp = await client.get(
            TOKEN_URL,
            params={"service": "registry"},
            headers={"Authorization": _basic_auth("t_user_a", "WrongPass!")},
        )
        assert resp.status_code == 401


# ── /docker/certs and /docker/jwks public endpoints ──────────────────────────

class TestPublicKeyEndpoints:
    async def test_certs_endpoint_returns_pem(self, client):
        """`/docker/certs` отдаёт self-signed X.509 сертификат (CERTIFICATE,
        не «голый» PUBLIC KEY) — он используется напрямую как
        Docker registry `rootcertbundle`."""
        resp = await client.get("/api/auth/v1/docker/certs")
        assert resp.status_code == 200
        body = resp.text
        assert body.startswith("-----BEGIN CERTIFICATE-----")
        assert "-----END CERTIFICATE-----" in body

    async def test_jwks_endpoint_returns_keys_array(self, client):
        resp = await client.get("/api/auth/v1/docker/jwks")
        assert resp.status_code == 200
        body = resp.json()
        assert "keys" in body
        assert isinstance(body["keys"], list) and body["keys"], "JWKS must publish at least one key"
        first = body["keys"][0]
        assert first.get("kty") == "RSA"
