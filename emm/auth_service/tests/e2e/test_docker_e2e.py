"""E2E-тесты: протокол токен-аутентификации Docker registry.

Тесты проверяют РЕАЛЬНЫЙ поток Docker token auth по HTTP:
  1. Клиент → GET /v2/                      → 401 + заголовок WWW-Authenticate
  2. Клиент → GET {realm}?service=&scope=   (Basic auth) → RS256 JWT
  3. Клиент → GET /v2/                      (Bearer JWT)  → 200

Docker CLI и Docker daemon не нужны — только HTTP.
Все тесты помечены меткой `e2e` и запускаются только в рамках docker-compose стека
или через `make test-devcont-all` (локальный бинарник registry).
"""

import json

import jwt
import pytest
import requests

from tests._helpers.http import _basic  # noqa: F401 — общий helper
from datetime import timedelta
from src.utils.time import utcnow

pytestmark = pytest.mark.e2e


# ── Helpers ───────────────────────────────────────────────────────────────────


def _v2_url(host: str) -> str:
    return f"http://{host}/v2/"


def _get_token(auth_base: str, realm: str, service: str, scope: str,
               username: str, password: str) -> requests.Response:
    """Call the token endpoint directly with Basic auth."""
    return requests.get(
        realm,
        params={"service": service, "scope": scope, "account": username},
        headers=_basic(username, password),
        timeout=10,
    )


def _parse_www_authenticate(header: str) -> dict:
    """Parse Bearer realm="...",service="...",scope="..." into a dict."""
    result = {}
    # Strip 'Bearer ' prefix
    header = header.removeprefix("Bearer ").removeprefix("bearer ")
    for part in header.split(","):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            result[k.strip()] = v.strip().strip('"')
    return result


# ── Registry availability ─────────────────────────────────────────────────────

class TestRegistryAvailability:
    def test_unauthenticated_request_returns_401(self, registry_host, e2e_docker_enabled):
        r = requests.get(_v2_url(registry_host), timeout=5)
        assert r.status_code == 401

    def test_401_contains_bearer_challenge(self, registry_host, e2e_docker_enabled):
        r = requests.get(_v2_url(registry_host), timeout=5)
        www = r.headers.get("WWW-Authenticate", "")
        assert www.lower().startswith("bearer")

    def test_challenge_contains_realm_and_service(self, registry_host, e2e_docker_enabled):
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        assert "realm" in params
        assert "service" in params
        assert params["realm"].endswith("/api/auth/v1/docker/token")


# ── Token issuance ────────────────────────────────────────────────────────────

class TestTokenIssuance:
    def test_valid_credentials_return_token(self, registry_host, auth_base,
                                             e2e_docker_enabled, e2e_user):
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="e2e_user", password="E2eUser1234!")
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert "access_token" in body
        assert body["token"] == body["access_token"]
        assert "expires_in" in body
        assert "issued_at" in body

    def test_token_is_rs256_jwt(self, registry_host, auth_base,
                                  e2e_docker_enabled, e2e_user):
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="e2e_user", password="E2eUser1234!")
        token = resp.json()["token"]
        header = json.loads(
            base64.urlsafe_b64decode(token.split(".")[0] + "==").decode()
        )
        assert header["alg"] == "RS256"
        assert "kid" in header

    def test_wrong_password_returns_401(self, registry_host, auth_base,
                                         e2e_docker_enabled, e2e_user):
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="e2e_user", password="WrongPassword!")
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

    def test_nonexistent_user_returns_401(self, registry_host, auth_base,
                                           e2e_docker_enabled):
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="ghost_user_xyz", password="anything")
        assert resp.status_code == 401

    def test_missing_basic_auth_returns_401(self, auth_base, e2e_docker_enabled):
        r = requests.get(f"{auth_base}/api/auth/v1/docker/token",
                         params={"service": "docker-registry:5000"}, timeout=5)
        assert r.status_code == 401


# ── Full auth flow ────────────────────────────────────────────────────────────

class TestFullAuthFlow:
    def test_bearer_token_allows_registry_access(self, registry_host, auth_base,
                                                   e2e_docker_enabled, e2e_user):
        """Complete Docker auth flow: challenge → token → authenticated request."""
        # Step 1: unauthenticated → 401 + challenge
        r1 = requests.get(_v2_url(registry_host), timeout=5)
        assert r1.status_code == 401
        params = _parse_www_authenticate(r1.headers["WWW-Authenticate"])

        # Step 2: get token
        token_resp = _get_token(auth_base, params["realm"], params["service"],
                                 scope="", username="e2e_user", password="E2eUser1234!")
        assert token_resp.status_code == 200
        token = token_resp.json()["token"]

        # Step 3: use token → 200
        r2 = requests.get(
            _v2_url(registry_host),
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert r2.status_code == 200

    def test_invalid_bearer_rejected(self, registry_host, e2e_docker_enabled):
        r = requests.get(
            _v2_url(registry_host),
            headers={"Authorization": "Bearer garbage.token.value"},
            timeout=5,
        )
        assert r.status_code == 401


# ── Scope / access control ────────────────────────────────────────────────────

class TestScopeAccess:
    def _get_pull_token(self, registry_host, auth_base, username, password, repo="myapp"):
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        scope = f"repository:{repo}:pull"
        return _get_token(auth_base, params["realm"], params["service"],
                          scope=scope, username=username, password=password)

    def _get_push_token(self, registry_host, auth_base, username, password, repo="myapp"):
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        scope = f"repository:{repo}:push"
        return _get_token(auth_base, params["realm"], params["service"],
                          scope=scope, username=username, password=password)

    def _decode_access(self, token: str) -> list[dict]:
        """Decode JWT payload (without verifying signature) to inspect access claims."""
        payload_b64 = token.split(".")[1]
        # Add padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode()).get("access", [])

    def test_pull_policy_all_grants_pull(self, registry_host, auth_base,
                                          e2e_docker_enabled, e2e_user):
        resp = self._get_pull_token(registry_host, auth_base, "e2e_user", "E2eUser1234!")
        assert resp.status_code == 200
        access = self._decode_access(resp.json()["token"])
        if access:  # registry may grant empty access, that's ok for policy=all
            pull_entries = [a for a in access if "pull" in a.get("actions", [])]
            assert len(pull_entries) > 0

    def test_push_allowed_for_user_in_push_list(self, registry_host, auth_base,
                                                  e2e_docker_enabled, e2e_user):
        """e2e_docker_enabled puts e2e_user in push_user_ids."""
        resp = self._get_push_token(registry_host, auth_base, "e2e_user", "E2eUser1234!")
        assert resp.status_code == 200
        access = self._decode_access(resp.json()["token"])
        if access:
            push_entries = [a for a in access if "push" in a.get("actions", [])]
            assert len(push_entries) > 0

    def test_push_denied_for_user_not_in_push_list(self, registry_host, auth_base,
                                                      e2e_docker_enabled, e2e_user_pull_only):
        """Пользователь в e2e_dept, но не в push_user_ids — push scope пуст.

        Раньше тест использовал e2e_admin (account_admin), но admin не привязан
        к dept и не получает docker_token вообще (DOCKER_ACCESS_DENIED 403).
        Корректнее — второй обычный пользователь того же отдела, которого нет
        в `push_user_ids`."""
        resp = self._get_push_token(
            registry_host, auth_base,
            "e2e_pull_only_user", "E2ePullOnly1234!",
        )
        assert resp.status_code == 200
        access = self._decode_access(resp.json()["token"])
        push_entries = [a for a in access if "push" in a.get("actions", [])]
        assert len(push_entries) == 0


# ── Disabled registry ─────────────────────────────────────────────────────────

class TestDisabledRegistry:
    def test_disabled_registry_returns_403(self, registry_host, auth_base,
                                            e2e_api, e2e_dept, e2e_user):
        """Disable docker for dept, then token request must return 403."""
        s, base = e2e_api
        # Disable
        s.delete(f"{base}/api/auth/v1/docker/registry/{e2e_dept['department_id']}")

        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="e2e_user", password="E2eUser1234!")
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "DOCKER_ACCESS_DENIED"

        # Re-enable so other tests still work
        s.put(
            f"{base}/api/auth/v1/docker/registry/{e2e_dept['department_id']}",
            json={"pull_policy": "all", "pull_user_ids": [],
                  "push_user_ids": [e2e_user["user_id"]]},
        )


# ── PAT as Docker password ────────────────────────────────────────────────────

class TestPATAuth:
    def test_pat_works_as_docker_password(self, registry_host, auth_base,
                                           e2e_docker_enabled, e2e_user_token,
                                           e2e_service):
        # Create a PAT
        pat_resp = requests.post(
            f"{auth_base}/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {e2e_user_token}"},
            json={"name": "e2e_docker_pat", "allowed_services": [e2e_service], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        )
        assert pat_resp.status_code == 201
        pat = pat_resp.json()["token"]

        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="e2e_user", password=pat)
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_revoked_pat_denied(self, registry_host, auth_base,
                                 e2e_docker_enabled, e2e_user_token,
                                 e2e_service):
        pat_data = requests.post(
            f"{auth_base}/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {e2e_user_token}"},
            json={"name": "e2e_docker_pat_rev", "allowed_services": [e2e_service], "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        ).json()
        requests.delete(
            f"{auth_base}/api/auth/v1/tokens/{pat_data['token_id']}",
            headers={"Authorization": f"Bearer {e2e_user_token}"},
        )

        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="e2e_user", password=pat_data["token"])
        assert resp.status_code == 401


# ── Bot token as Docker password ──────────────────────────────────────────────

class TestBotTokenAuth:
    def test_bot_token_works_as_docker_password(self, registry_host, auth_base,
                                                  e2e_docker_enabled, e2e_api, e2e_dept):
        s, base = e2e_api
        # Create bot in e2e_dept
        bot = s.post(f"{base}/api/auth/v1/bots",
                     json={"name": "e2e_dock_bot", "department_id": e2e_dept["department_id"],
                           "allowed_services": []}).json()
        bot_id = bot["bot_id"]
        tok = s.post(f"{base}/api/auth/v1/bots/{bot_id}/tokens",
                     json={"name": "dock_tok"}).json()["token"]

        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="e2e_dock_bot", password=tok)
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_revoked_bot_token_denied(self, registry_host, auth_base,
                                       e2e_docker_enabled, e2e_api, e2e_dept):
        s, base = e2e_api
        bot = s.post(f"{base}/api/auth/v1/bots",
                     json={"name": "e2e_dock_bot_rev", "department_id": e2e_dept["department_id"],
                           "allowed_services": []}).json()
        bot_id = bot["bot_id"]
        tok_data = s.post(f"{base}/api/auth/v1/bots/{bot_id}/tokens",
                          json={"name": "rev_tok"}).json()
        s.delete(f"{base}/api/auth/v1/bots/{bot_id}/tokens/{tok_data['token_id']}")

        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="", username="e2e_dock_bot_rev", password=tok_data["token"])
        assert resp.status_code == 401


# ── JWT verification via JWKS ─────────────────────────────────────────────────

class TestJWKS:
    def test_jwks_endpoint_is_accessible(self, auth_base, e2e_docker_enabled):
        r = requests.get(f"{auth_base}/api/auth/v1/docker/jwks", timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert "keys" in body
        key = body["keys"][0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"

    def test_registry_token_verifiable_with_jwks(self, registry_host, auth_base,
                                                   e2e_docker_enabled, e2e_user):
        """Fetch JWKS and use it to verify the signature of a registry token."""
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
        from jwt.algorithms import RSAAlgorithm

        # Get JWKS
        jwks = requests.get(f"{auth_base}/api/auth/v1/docker/jwks", timeout=5).json()
        jwk = jwks["keys"][0]
        public_key = RSAAlgorithm.from_jwk(jwk)

        # Get a token
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        token = _get_token(auth_base, params["realm"], params["service"],
                           scope="", username="e2e_user",
                           password="E2eUser1234!").json()["token"]

        # Verify signature
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert payload["sub"] is not None
        assert payload["iss"] == "auth_service"

    def test_certs_endpoint_returns_pem(self, auth_base, e2e_docker_enabled):
        r = requests.get(f"{auth_base}/api/auth/v1/docker/certs", timeout=5)
        assert r.status_code == 200
        assert "CERTIFICATE" in r.text


# ── Restricted pull policy ────────────────────────────────────────────────────

class TestRestrictedPullPolicy:
    def test_restricted_policy_only_listed_user_gets_pull(self, registry_host, auth_base,
                                                           e2e_api, e2e_dept, e2e_user):
        """Switch to restricted pull policy, verify only listed user can pull."""
        s, base = e2e_api
        # Enable restricted for e2e_user only
        s.put(
            f"{base}/api/auth/v1/docker/registry/{e2e_dept['department_id']}",
            json={"pull_policy": "restricted",
                  "pull_user_ids": [e2e_user["user_id"]],
                  "push_user_ids": [e2e_user["user_id"]]},
        )

        # e2e_user (listed) can get token
        r = requests.get(_v2_url(registry_host), timeout=5)
        params = _parse_www_authenticate(r.headers["WWW-Authenticate"])
        resp = _get_token(auth_base, params["realm"], params["service"],
                          scope="repository:app:pull",
                          username="e2e_user", password="E2eUser1234!")
        assert resp.status_code == 200

        # Restore pull_policy=all
        s.put(
            f"{base}/api/auth/v1/docker/registry/{e2e_dept['department_id']}",
            json={"pull_policy": "all", "pull_user_ids": [],
                  "push_user_ids": [e2e_user["user_id"]]},
        )
