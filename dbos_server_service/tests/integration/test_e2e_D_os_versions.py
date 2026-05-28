"""E2E cluster D — /os-versions catalog.

Read endpoints (GET list / by id / by name) are PUBLIC — no auth required.
Write endpoints (POST/PATCH/DELETE) are gated by the matrix:
  * `os_version.create` / `update` / `delete` — admin role has all by seed,
    operator/reader do not.
  * Permission catalog DOES NOT include `os_version.view` — `view` was orphaned
    in migration c1a9f2b7e4d8 (catalog is public).

Endpoint paths:
  GET    /api/server/v1/os-versions
  POST   /api/server/v1/os-versions
  GET    /api/server/v1/os-versions/{id}
  PATCH  /api/server/v1/os-versions/{id}
  DELETE /api/server/v1/os-versions/{id}
  GET    /api/server/v1/os-versions/by-name/{name}
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration._helpers_D_server import (
    TenantBundle,
    build_tenants,
    _u,
)


_OSV = "/api/server/v1/os-versions"
_PERMS_CATALOG = "/api/server/v1/permissions/catalog"


@pytest.fixture(scope="class")
def tenants(
    auth_client: httpx.Client, admin_token: str, make_user, login_token,
) -> TenantBundle:
    return build_tenants(
        auth_client=auth_client, admin_token=admin_token,
        make_user=make_user, login_token=login_token,
    )


@pytest.mark.usefixtures("reset_state")
class TestOsVersionPublicRead:
    """GET endpoints are public — no auth header required."""

    def test_list_no_auth_200(self, server_client):
        r = server_client.get(_OSV)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "items" in body
        assert "total" in body

    def test_get_by_id_no_auth_404_for_missing(self, server_client):
        """Public read still resolves to 404 for missing rows (no auth, no 401)."""
        r = server_client.get(f"{_OSV}/osv_nonexistent_zzz")
        assert r.status_code == 404, r.text

    def test_get_by_name_no_auth_404_for_missing(self, server_client):
        r = server_client.get(f"{_OSV}/by-name/nonexistent-os-{_u()}")
        assert r.status_code == 404, r.text


@pytest.mark.usefixtures("reset_state")
class TestOsVersionWriteMatrix:
    """Write endpoints obey the permission matrix (auth required)."""

    def test_admin_create_201(self, server_client, tenants: TenantBundle):
        name = f"astra-test-{_u()}"
        r = server_client.post(
            _OSV, json={"name": name, "description": "for E2E"},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["name"] == name

    def test_operator_cannot_create_403(self, server_client, tenants: TenantBundle):
        r = server_client.post(
            _OSV, json={"name": f"op-os-{_u()}"},
            headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_reader_cannot_create_403(self, server_client, tenants: TenantBundle):
        r = server_client.post(
            _OSV, json={"name": f"rd-os-{_u()}"},
            headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_create_no_auth_401(self, server_client):
        """Write needs auth."""
        r = server_client.post(_OSV, json={"name": f"noauth-{_u()}"})
        assert r.status_code == 401, r.text

    def test_admin_patch(self, server_client, tenants: TenantBundle):
        name = f"patchos-{_u()}"
        created = server_client.post(
            _OSV, json={"name": name},
            headers=tenants.admin_a.headers(),
        ).json()
        r = server_client.patch(
            f"{_OSV}/{created['id']}",
            json={"description": "patched"},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["description"] == "patched"

    def test_reader_cannot_patch_403(self, server_client, tenants: TenantBundle):
        name = f"rdpatch-{_u()}"
        created = server_client.post(
            _OSV, json={"name": name},
            headers=tenants.admin_a.headers(),
        ).json()
        r = server_client.patch(
            f"{_OSV}/{created['id']}",
            json={"description": "no"},
            headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_admin_delete(self, server_client, tenants: TenantBundle):
        name = f"delos-{_u()}"
        created = server_client.post(
            _OSV, json={"name": name}, headers=tenants.admin_a.headers(),
        ).json()
        r = server_client.delete(
            f"{_OSV}/{created['id']}", headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        # follow-up GET → 404
        r2 = server_client.get(f"{_OSV}/{created['id']}")
        assert r2.status_code == 404, r2.text

    def test_create_duplicate_name_409(self, server_client, tenants: TenantBundle):
        name = f"dupos-{_u()}"
        r1 = server_client.post(
            _OSV, json={"name": name}, headers=tenants.admin_a.headers(),
        )
        assert r1.status_code == 201
        r2 = server_client.post(
            _OSV, json={"name": name}, headers=tenants.admin_a.headers(),
        )
        assert r2.status_code == 409, r2.text


@pytest.mark.usefixtures("reset_state")
class TestPermissionCatalogOsVersion:
    """Permission catalog must NOT advertise `os_version.view` — orphaned in migration c1a9f2b7e4d8."""

    def test_catalog_has_os_version_without_view(
        self, server_client, tenants: TenantBundle,
    ):
        # Catalog needs auth + admin/dept_admin-equivalent (permission.view)
        r = server_client.get(
            _PERMS_CATALOG, headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        catalog = r.json()
        # Find os_version entity
        os_version = None
        for entity in catalog:
            if entity.get("entity_type") == "os_version":
                os_version = entity
                break
        assert os_version is not None, "os_version entity missing from catalog"
        action_names = {
            a.get("action") if isinstance(a, dict) else a
            for a in os_version.get("actions", [])
        }
        # `view` MUST NOT be advertised
        assert "view" not in action_names, (
            f"os_version.view should be orphaned (catalog public), got actions {action_names}"
        )
        # Write actions remain
        assert {"create", "update", "delete"} <= action_names, (
            f"missing write actions in os_version catalog: {action_names}"
        )
