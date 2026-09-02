"""E2E cluster D — busy-lease (acquire / release).

Note on the URL shape:

    The TODO uses `/servers/{id}/busy_acquire` / `/busy_release`, but the
    actual implementation in `endpoints/servers.py` exposes them as a
    single resource:
        POST   /api/server/v1/servers/{id}/busy   — acquire
        DELETE /api/server/v1/servers/{id}/busy   — release

    The endpoint docstrings name the actions `server.acquire` / `server.release`
    and gate them with action grants `busy_acquire` / `busy_release`. Tests
    follow the actual paths.

Coverage:
  * acquire (POST /busy): admin/operator allowed (busy_acquire grant);
    reader/guest denied.
  * Second acquire → 409 SERVER_ALREADY_BUSY (single-tenant lock).
  * Release (DELETE /busy): clears busy_state. Release-when-free → 409.
  * Cross-dept acquire → 404.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration._helpers_D_server import (
    TenantBundle,
    build_tenants,
    create_server_as,
    expect_audit_event,
)


_SERVERS = "/api/server/v1/servers"


@pytest.fixture(scope="class")
def tenants(
    auth_client: httpx.Client, admin_token: str, make_user, login_token,
) -> TenantBundle:
    return build_tenants(
        auth_client=auth_client, admin_token=admin_token,
        make_user=make_user, login_token=login_token,
    )


@pytest.mark.usefixtures("reset_state")
class TestBusyAcquireRelease:
    def test_admin_acquire_success(
        self, server_client, loging_db_engine, tenants: TenantBundle,
    ):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={"purpose": "stress run"},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["busy_state"] == "busy"
        assert data["busy_user_id"] == tenants.admin_a.user_id
        ev = expect_audit_event(
            loging_db_engine, action="server.acquire",
            target_id=srv["id"], status="success",
        )
        assert ev["actor_id"] == tenants.admin_a.user_id

    def test_operator_can_acquire(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={}, headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 200, r.text

    def test_reader_cannot_acquire_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={}, headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_guest_cannot_acquire_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={}, headers=tenants.guest_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_second_acquire_409(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r1 = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={}, headers=tenants.admin_a.headers(),
        )
        assert r1.status_code == 200
        # Second acquire by operator (different user) — single-tenant lock holds
        r2 = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={}, headers=tenants.operator_a.headers(),
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["error_code"] == "SERVER_ALREADY_BUSY"

    def test_release_after_acquire(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r1 = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={}, headers=tenants.admin_a.headers(),
        )
        assert r1.status_code == 200
        r2 = server_client.delete(
            f"{_SERVERS}/{srv['id']}/busy",
            headers=tenants.admin_a.headers(),
        )
        assert r2.status_code == 200, r2.text
        data = r2.json()
        assert data["busy_state"] == "free"
        assert data["busy_user_id"] is None
        # After release, another acquire succeeds
        r3 = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={}, headers=tenants.operator_a.headers(),
        )
        assert r3.status_code == 200, r3.text

    def test_release_when_free_409(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.delete(
            f"{_SERVERS}/{srv['id']}/busy",
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "SERVER_NOT_BUSY"

    def test_reader_cannot_release_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        # First acquire as admin so there's something to release.
        ack = server_client.post(
            f"{_SERVERS}/{srv['id']}/busy",
            json={}, headers=tenants.admin_a.headers(),
        )
        assert ack.status_code == 200
        r = server_client.delete(
            f"{_SERVERS}/{srv['id']}/busy",
            headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_cross_dept_acquire_404(self, server_client, tenants: TenantBundle):
        srv_in_a = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            f"{_SERVERS}/{srv_in_a['id']}/busy",
            json={}, headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text
        assert r.json()["error_code"] == "SERVER_NOT_FOUND"

    def test_cross_dept_release_404(self, server_client, tenants: TenantBundle):
        srv_in_a = create_server_as(server_client, tenants.admin_a)
        # Acquire under admin_a first so there's a real lock.
        server_client.post(
            f"{_SERVERS}/{srv_in_a['id']}/busy",
            json={}, headers=tenants.admin_a.headers(),
        )
        r = server_client.delete(
            f"{_SERVERS}/{srv_in_a['id']}/busy",
            headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text
