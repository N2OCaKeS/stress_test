"""E2E cluster D — /servers/{id}/ipmi CRUD + view_credentials.

Endpoint paths:
  POST   /api/server/v1/servers/{id}/ipmi
  GET    /api/server/v1/servers/{id}/ipmi
  PATCH  /api/server/v1/servers/{id}/ipmi
  DELETE /api/server/v1/servers/{id}/ipmi

Invariants exercised:
  * matrix: admin/operator create; reader/guest cannot
  * UNIQUE(server_id): re-register on same server → 409 IPMI_DUPLICATE
  * GET without view_credentials → password_b64 == null
  * GET with view_credentials (admin via seed) → b64 + CRITICAL audit
    `ipmi_controller.credentials_revealed`
  * Cross-dept GET/PATCH/DELETE → 404
  * Schema sanity: response does NOT include `bmc_vendor` (field doesn't exist)
"""

from __future__ import annotations

import base64

import httpx
import pytest

from tests.integration._helpers_D_server import (
    TenantBundle,
    build_tenants,
    create_server_as,
    db_row,
    expect_audit_event,
    make_ipmi_body,
    poll_audit_event,
)


def _ipmi_url(server_id: str) -> str:
    return f"/api/server/v1/servers/{server_id}/ipmi"


@pytest.fixture(scope="class")
def tenants(
    auth_client: httpx.Client, admin_token: str, make_user, login_token,
) -> TenantBundle:
    return build_tenants(
        auth_client=auth_client, admin_token=admin_token,
        make_user=make_user, login_token=login_token,
    )


@pytest.mark.usefixtures("reset_state")
class TestIpmiCreate:
    def test_admin_create_201(
        self, server_client, server_db_engine, tenants: TenantBundle,
    ):
        srv = create_server_as(server_client, tenants.admin_a)
        body = make_ipmi_body()
        r = server_client.post(
            _ipmi_url(srv["id"]), json=body, headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["server_id"] == srv["id"]
        assert data["kind"] == body["kind"]
        assert data["endpoint_url"] == body["endpoint_url"]
        assert data["username"] == body["username"]
        # No plaintext in response
        assert "password" not in data
        # bmc_vendor must not exist in the schema
        assert "bmc_vendor" not in data
        # DB row
        assert db_row(server_db_engine, "ipmi_controllers", server_id=srv["id"]) is not None

    def test_operator_create(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            _ipmi_url(srv["id"]), json=make_ipmi_body(),
            headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 201, r.text

    def test_reader_cannot_create_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            _ipmi_url(srv["id"]), json=make_ipmi_body(),
            headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_duplicate_ipmi_409(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r1 = server_client.post(
            _ipmi_url(srv["id"]), json=make_ipmi_body(),
            headers=tenants.admin_a.headers(),
        )
        assert r1.status_code == 201
        r2 = server_client.post(
            _ipmi_url(srv["id"]), json=make_ipmi_body(),
            headers=tenants.admin_a.headers(),
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["error_code"] == "IPMI_DUPLICATE"

    def test_cross_dept_create_404(self, server_client, tenants: TenantBundle):
        srv_in_a = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            _ipmi_url(srv_in_a["id"]), json=make_ipmi_body(),
            headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text


@pytest.mark.usefixtures("reset_state")
class TestIpmiGet:
    def test_get_without_view_credentials_returns_null(
        self, server_client, loging_db_engine, tenants: TenantBundle,
    ):
        srv = create_server_as(server_client, tenants.admin_a)
        server_client.post(
            _ipmi_url(srv["id"]),
            json=make_ipmi_body(username="adminbmc", password="BmcPass1!"),
            headers=tenants.admin_a.headers(),
        )
        # operator has `view` (default seed) but not `view_credentials`
        r = server_client.get(
            _ipmi_url(srv["id"]), headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["password_b64"] is None
        assert "bmc_vendor" not in body
        # No credentials_revealed audit for the non-credentialed reader
        ev = poll_audit_event(
            loging_db_engine,
            action="ipmi_controller.credentials_revealed",
            actor_id=tenants.operator_a.user_id,
            retries=4, delay=0.25,
        )
        assert ev is None, f"unexpected credentials_revealed audit: {ev}"

    def test_get_with_view_credentials_returns_b64_and_critical_audit(
        self, server_client, loging_db_engine, tenants: TenantBundle,
    ):
        srv = create_server_as(server_client, tenants.admin_a)
        password = "TopSecretBmc1!"
        server_client.post(
            _ipmi_url(srv["id"]),
            json=make_ipmi_body(username="adminbmc", password=password),
            headers=tenants.admin_a.headers(),
        )
        r = server_client.get(
            _ipmi_url(srv["id"]), headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        b64 = r.json()["password_b64"]
        assert b64 is not None
        assert base64.b64decode(b64).decode("utf-8") == password
        ev = expect_audit_event(
            loging_db_engine,
            action="ipmi_controller.credentials_revealed",
            actor_id=tenants.admin_a.user_id,
            status="success",
        )
        assert ev["severity"] == "CRITICAL"

    def test_guest_cannot_get_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        server_client.post(
            _ipmi_url(srv["id"]), json=make_ipmi_body(),
            headers=tenants.admin_a.headers(),
        )
        r = server_client.get(
            _ipmi_url(srv["id"]), headers=tenants.guest_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_cross_dept_get_404(self, server_client, tenants: TenantBundle):
        srv_a = create_server_as(server_client, tenants.admin_a)
        server_client.post(
            _ipmi_url(srv_a["id"]), json=make_ipmi_body(),
            headers=tenants.admin_a.headers(),
        )
        r = server_client.get(
            _ipmi_url(srv_a["id"]), headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text

    def test_get_no_controller_404(self, server_client, tenants: TenantBundle):
        """Server exists, IPMI not registered → 404 NO_IPMI_CONTROLLER."""
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.get(
            _ipmi_url(srv["id"]), headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "NO_IPMI_CONTROLLER"


@pytest.mark.usefixtures("reset_state")
class TestIpmiPatchDelete:
    def test_admin_patch(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        server_client.post(
            _ipmi_url(srv["id"]),
            json=make_ipmi_body(endpoint_url="https://old.example", username="u1"),
            headers=tenants.admin_a.headers(),
        )
        r = server_client.patch(
            _ipmi_url(srv["id"]),
            json={"endpoint_url": "https://new.example", "username": "u2"},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["endpoint_url"] == "https://new.example"
        assert data["username"] == "u2"

    def test_reader_cannot_patch_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        server_client.post(
            _ipmi_url(srv["id"]), json=make_ipmi_body(),
            headers=tenants.admin_a.headers(),
        )
        r = server_client.patch(
            _ipmi_url(srv["id"]), json={"username": "x"},
            headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_cross_dept_patch_404(self, server_client, tenants: TenantBundle):
        srv_a = create_server_as(server_client, tenants.admin_a)
        server_client.post(
            _ipmi_url(srv_a["id"]), json=make_ipmi_body(),
            headers=tenants.admin_a.headers(),
        )
        r = server_client.patch(
            _ipmi_url(srv_a["id"]), json={"username": "x"},
            headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text

    def test_admin_delete(
        self, server_client, server_db_engine, tenants: TenantBundle,
    ):
        srv = create_server_as(server_client, tenants.admin_a)
        server_client.post(
            _ipmi_url(srv["id"]), json=make_ipmi_body(),
            headers=tenants.admin_a.headers(),
        )
        r = server_client.delete(
            _ipmi_url(srv["id"]), headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        assert db_row(server_db_engine, "ipmi_controllers", server_id=srv["id"]) is None

    def test_operator_cannot_delete_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        server_client.post(
            _ipmi_url(srv["id"]), json=make_ipmi_body(),
            headers=tenants.admin_a.headers(),
        )
        r = server_client.delete(
            _ipmi_url(srv["id"]), headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 403, r.text
