"""Cross-service idempotency poisoning гард на `POST /events`.

Дедуп `record()` берёт ключом `(payload.service, idempotency_key)` —
держатель валидного API-ключа `auth_service` мог послать payload c
`service="server_service"` + чужим idempotency_key и затирать
audit-trail соседнего сервиса. Симметрично path-guard'у
`SERVICE_IDENTITY_PATH_MISMATCH` в `POST /services/{service}/events`.
"""

from tests.conftest import TEST_API_KEY, make_event

EVENTS_URL = "/api/logging/v1/events"


class TestServiceIdentityPayloadMismatch:
    def test_mismatched_identity_payload_rejected_403(self, client, auth_headers):
        """`X-Service-Identity: auth_service` + `payload.service=server_service`
        → 403 `SERVICE_IDENTITY_PAYLOAD_MISMATCH`."""
        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        r = client.post(
            EVENTS_URL,
            json=make_event(service="server_service", idempotency_key="poison-1"),
            headers=headers,
        )
        assert r.status_code == 403, r.text
        body = r.json()
        assert body["error_code"] == "SERVICE_IDENTITY_PAYLOAD_MISMATCH"
        assert "payload.service" in body["message"]

    def test_matching_identity_payload_accepted(self, client, auth_headers):
        """`X-Service-Identity: auth_service` + `payload.service=auth_service` → 201."""
        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        r = client.post(
            EVENTS_URL,
            json=make_event(service="auth_service", idempotency_key="legit-1"),
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["id"]

    def test_server_service_under_own_identity_accepted(self, client, auth_headers):
        """Caller, представляющийся `server_service`, может писать события
        своего же сервиса."""
        headers = {**auth_headers, "X-Service-Identity": "server_service"}
        r = client.post(
            EVENTS_URL,
            json=make_event(service="server_service", idempotency_key="legit-2"),
            headers=headers,
        )
        assert r.status_code == 201, r.text

    def test_missing_identity_rejected_by_dependency(self, client):
        """Без `X-Service-Identity` `require_service_token` отбивает 401 ещё
        до guard'а — payload-guard срабатывает только когда identity stash'нут."""
        r = client.post(
            EVENTS_URL,
            json=make_event(service="server_service"),
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
        assert r.status_code == 401
        assert r.json()["error_code"] == "MISSING_SERVICE_IDENTITY"

    def test_identity_case_normalised_against_payload(self, client, auth_headers):
        """Identity нормализуется (lower/NFKC); payload.service тоже нормализуется
        тем же путём. `AUTH_SERVICE` в header матчит `auth_service` в payload."""
        headers = {**auth_headers, "X-Service-Identity": "AUTH_SERVICE"}
        r = client.post(
            EVENTS_URL,
            json=make_event(service="auth_service", idempotency_key="case-1"),
            headers=headers,
        )
        assert r.status_code == 201, r.text
