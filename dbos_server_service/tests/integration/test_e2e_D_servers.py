"""E2E cluster D — POST/GET/PATCH/DELETE /servers + role matrix + cross-dept.

Endpoint paths (server_service):
  POST   /api/server/v1/servers
  GET    /api/server/v1/servers
  GET    /api/server/v1/servers/{server_id}
  PATCH  /api/server/v1/servers/{server_id}
  DELETE /api/server/v1/servers/{server_id}

Invariants exercised here:
  * `admin` / `operator` can create, `reader` / `guest` cannot
  * `admin` / `operator` / `reader` can view, `guest` cannot
  * `admin` only can DELETE
  * Cross-dept GET/PATCH/DELETE → 404 (anti-enumeration), not 403
  * POST in another dept → 403 DEPARTMENT_ISOLATION
  * Storage: invariant of at most one system disk → 422; size_gb only (no kind)
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration._helpers_D_server import (
    TenantBundle,
    build_tenants,
    create_server_as,
    db_count,
    db_row,
    expect_audit_event,
    make_server_body,
    next_ip,
    poll_audit_event,
    require_audit_or_xfail,
)


_SERVERS = "/api/server/v1/servers"


@pytest.fixture(scope="class")
def tenants(
    auth_client: httpx.Client,
    admin_token: str,
    make_user,
    login_token,
) -> TenantBundle:
    return build_tenants(
        auth_client=auth_client, admin_token=admin_token,
        make_user=make_user, login_token=login_token,
    )


@pytest.mark.usefixtures("reset_state")
class TestServerCreate:
    """POST /servers — matrix + dept-isolation + storage invariants."""

    def test_admin_create_201_writes_db_and_audit(
        self, server_client: httpx.Client, server_db_engine, loging_db_engine,
        tenants: TenantBundle,
    ):
        body = make_server_body(department_id=tenants.dept_a)
        r = server_client.post(_SERVERS, json=body, headers=tenants.admin_a.headers())
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["hostname"] == body["hostname"]
        assert data["department_id"] == tenants.dept_a
        assert data["created_by"] == tenants.admin_a.user_id
        # DB-state: row present
        row = db_row(server_db_engine, "servers", id=data["id"])
        assert row is not None
        assert row["hostname"] == body["hostname"]
        assert row["department_id"] == tenants.dept_a
        # Audit landed
        ev = expect_audit_event(
            loging_db_engine, action="server.create",
            target_id=data["id"], status="success",
        )
        assert ev["actor_id"] == tenants.admin_a.user_id

    def test_operator_can_create(self, server_client, tenants: TenantBundle):
        body = make_server_body(department_id=tenants.dept_a)
        r = server_client.post(_SERVERS, json=body, headers=tenants.operator_a.headers())
        assert r.status_code == 201, r.text

    def test_reader_cannot_create_403(
        self, server_client, loging_db_engine, tenants: TenantBundle,
    ):
        body = make_server_body(department_id=tenants.dept_a)
        r = server_client.post(_SERVERS, json=body, headers=tenants.reader_a.headers())
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "PERMISSION_DENIED"
        # Denied audit
        ev = poll_audit_event(
            loging_db_engine, action="server.create",
            actor_id=tenants.reader_a.user_id, status="denied",
        )
        ev = require_audit_or_xfail(loging_db_engine, "server.create", ev)
        assert ev["allowed"] is False

    def test_guest_cannot_create_403(self, server_client, tenants: TenantBundle):
        body = make_server_body(department_id=tenants.dept_a)
        r = server_client.post(_SERVERS, json=body, headers=tenants.guest_a.headers())
        assert r.status_code == 403, r.text

    def test_cross_dept_create_403_department_isolation(
        self, server_client, loging_db_engine, tenants: TenantBundle,
    ):
        """admin of dept_a tries to create server in dept_b → 403 DEPARTMENT_ISOLATION."""
        body = make_server_body(department_id=tenants.dept_b)
        r = server_client.post(_SERVERS, json=body, headers=tenants.admin_a.headers())
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "DEPARTMENT_ISOLATION"

    def test_duplicate_hostname_409(self, server_client, tenants: TenantBundle):
        body = make_server_body(department_id=tenants.dept_a)
        r1 = server_client.post(_SERVERS, json=body, headers=tenants.admin_a.headers())
        assert r1.status_code == 201
        # Same hostname (and same IP — also unique) → 409
        body2 = make_server_body(
            department_id=tenants.dept_a,
            hostname=body["hostname"],
        )
        r2 = server_client.post(_SERVERS, json=body2, headers=tenants.admin_a.headers())
        assert r2.status_code == 409, r2.text
        assert r2.json()["error_code"] == "SERVER_DUPLICATE"

    def test_unauthenticated_401(self, server_client, tenants: TenantBundle):
        body = make_server_body(department_id=tenants.dept_a)
        r = server_client.post(_SERVERS, json=body)
        assert r.status_code == 401, r.text

    def test_create_with_storage_size_only(
        self, server_client, server_db_engine, tenants: TenantBundle,
    ):
        body = make_server_body(
            department_id=tenants.dept_a,
            storage=[
                {"slot": "system", "size_gb": 100},
                {"slot": "disk1", "size_gb": 500, "is_system": False},
            ],
        )
        r = server_client.post(_SERVERS, json=body, headers=tenants.admin_a.headers())
        assert r.status_code == 201, r.text
        data = r.json()
        storage = data["storage"]
        assert len(storage) == 2
        slots = sorted(d["slot"] for d in storage)
        assert slots == ["disk1", "system"]
        # System slot is auto-marked system
        system_disk = next(d for d in storage if d["slot"] == "system")
        assert system_disk["is_system"] is True
        assert system_disk["size_gb"] == 100
        # No "kind" field — disks expose slot/size_gb/is_system/model
        assert "kind" not in storage[0]
        # DB: 2 disks for this server
        assert db_count(server_db_engine, "server_disks", server_id=data["id"]) == 2

    def test_create_two_system_disks_422(self, server_client, tenants: TenantBundle):
        body = make_server_body(
            department_id=tenants.dept_a,
            storage=[
                {"slot": "disk1", "size_gb": 200, "is_system": True},
                {"slot": "disk2", "size_gb": 300, "is_system": True},
            ],
        )
        r = server_client.post(_SERVERS, json=body, headers=tenants.admin_a.headers())
        assert r.status_code == 422, r.text
        text_lower = r.text.lower()
        assert "system disk" in text_lower or "one system" in text_lower

    def test_create_duplicate_slot_422(self, server_client, tenants: TenantBundle):
        body = make_server_body(
            department_id=tenants.dept_a,
            storage=[
                {"slot": "disk1", "size_gb": 100},
                {"slot": "disk1", "size_gb": 200},
            ],
        )
        r = server_client.post(_SERVERS, json=body, headers=tenants.admin_a.headers())
        assert r.status_code == 422, r.text


@pytest.mark.usefixtures("reset_state")
class TestServerGet:
    """GET /servers/{id} + GET /servers (list) — role matrix + 404 anti-enum."""

    def test_admin_can_get_own(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.get(
            f"{_SERVERS}/{srv['id']}", headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["id"] == srv["id"]

    def test_reader_can_get_own(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.get(
            f"{_SERVERS}/{srv['id']}", headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 200, r.text

    def test_operator_can_get_own(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.get(
            f"{_SERVERS}/{srv['id']}", headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 200, r.text

    def test_guest_cannot_get_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.get(
            f"{_SERVERS}/{srv['id']}", headers=tenants.guest_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_cross_dept_get_returns_404_not_403(
        self, server_client, loging_db_engine, tenants: TenantBundle,
    ):
        """Critical invariant: chosen 404 over 403 to prevent enumeration."""
        srv_in_a = create_server_as(server_client, tenants.admin_a)
        r = server_client.get(
            f"{_SERVERS}/{srv_in_a['id']}", headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, (
            f"cross-dept GET must be 404 (anti-enumeration), got {r.status_code}: {r.text}"
        )
        assert r.json()["error_code"] == "SERVER_NOT_FOUND"

    def test_get_nonexistent_404(self, server_client, tenants: TenantBundle):
        r = server_client.get(
            f"{_SERVERS}/srv_nonexistent_zzz", headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 404, r.text

    def test_list_only_own_dept(
        self, server_client, tenants: TenantBundle,
    ):
        a1 = create_server_as(server_client, tenants.admin_a)
        a2 = create_server_as(server_client, tenants.admin_a)
        # one server in dept_b
        b1 = create_server_as(server_client, tenants.admin_b)
        # admin_a should see a1+a2 but not b1
        r = server_client.get(_SERVERS, headers=tenants.admin_a.headers())
        assert r.status_code == 200
        ids = {item["id"] for item in r.json()["items"]}
        assert a1["id"] in ids and a2["id"] in ids
        assert b1["id"] not in ids
        # admin_b symmetric
        r = server_client.get(_SERVERS, headers=tenants.admin_b.headers())
        assert r.status_code == 200
        ids_b = {item["id"] for item in r.json()["items"]}
        assert b1["id"] in ids_b
        assert a1["id"] not in ids_b
        assert a2["id"] not in ids_b


@pytest.mark.usefixtures("reset_state")
class TestServerUpdate:
    """PATCH /servers/{id} — role gate + dept isolation + storage update."""

    def test_admin_can_patch(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.patch(
            f"{_SERVERS}/{srv['id']}",
            json={"display_name": "patched-name"},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["display_name"] == "patched-name"

    def test_operator_can_patch(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.patch(
            f"{_SERVERS}/{srv['id']}",
            json={"location": "rack-42"},
            headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 200, r.text

    def test_reader_cannot_patch_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.patch(
            f"{_SERVERS}/{srv['id']}",
            json={"display_name": "nope"},
            headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_cross_dept_patch_returns_404(self, server_client, tenants: TenantBundle):
        srv_in_a = create_server_as(server_client, tenants.admin_a)
        r = server_client.patch(
            f"{_SERVERS}/{srv_in_a['id']}",
            json={"display_name": "stealthy"},
            headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text

    def test_partial_storage_update(
        self, server_client, server_db_engine, tenants: TenantBundle,
    ):
        srv = create_server_as(
            server_client, tenants.admin_a,
            storage=[
                {"slot": "system", "size_gb": 100},
                {"slot": "disk1", "size_gb": 250},
            ],
        )
        # Replace storage entirely
        r = server_client.patch(
            f"{_SERVERS}/{srv['id']}",
            json={"storage": [
                {"slot": "system", "size_gb": 200},
                {"slot": "disk1", "size_gb": 500},
                {"slot": "disk2", "size_gb": 1000},
            ]},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        new = r.json()["storage"]
        assert len(new) == 3
        # DB row check: 3 disks
        assert db_count(server_db_engine, "server_disks", server_id=srv["id"]) == 3
        sys_disk = next(d for d in new if d["slot"] == "system")
        assert sys_disk["size_gb"] == 200

    def test_patch_two_system_disks_422(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.patch(
            f"{_SERVERS}/{srv['id']}",
            json={"storage": [
                {"slot": "disk1", "size_gb": 100, "is_system": True},
                {"slot": "disk2", "size_gb": 200, "is_system": True},
            ]},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 422, r.text


@pytest.mark.usefixtures("reset_state")
class TestServerDelete:
    """DELETE /servers/{id} — admin-only + cross-dept 404."""

    def test_admin_can_delete(
        self, server_client, server_db_engine, loging_db_engine,
        tenants: TenantBundle,
    ):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.delete(
            f"{_SERVERS}/{srv['id']}", headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        # row gone
        assert db_row(server_db_engine, "servers", id=srv["id"]) is None
        # CRITICAL audit
        ev = expect_audit_event(
            loging_db_engine, action="server.delete",
            target_id=srv["id"], status="success",
        )
        assert ev["severity"] == "CRITICAL"

    def test_operator_cannot_delete_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.delete(
            f"{_SERVERS}/{srv['id']}", headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_reader_cannot_delete_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.delete(
            f"{_SERVERS}/{srv['id']}", headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_cross_dept_delete_returns_404(self, server_client, tenants: TenantBundle):
        srv_in_a = create_server_as(server_client, tenants.admin_a)
        r = server_client.delete(
            f"{_SERVERS}/{srv_in_a['id']}", headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text
