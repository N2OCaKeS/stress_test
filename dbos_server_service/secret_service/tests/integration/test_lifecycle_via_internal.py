"""Internal lifecycle endpoints: cascade при delete_user/delete_dept/revoke.

Auth для `/internal/*` — shared bearer `SERVICE_API_KEY`, не introspect.
В conftest он выставлен в `internal-test-key`.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import auth_header

BASE = "/api/secret/v1"
INTERNAL_KEY = "internal-test-key"

pytestmark = pytest.mark.asyncio


def _internal_header() -> dict:
    # `/internal/lifecycle/*` обслуживает строго auth_service:
    # `require_caller_identity("auth_service")` смотрит в `X-Service-Identity`,
    # без него — 401 INTERNAL_AUTH_REQUIRED, даже при валидном bearer.
    return {
        "Authorization": f"Bearer {INTERNAL_KEY}",
        "X-Service-Identity": "auth_service",
    }


async def test_user_deleted_cascade_mix_of_blocked_and_deleted(
    client, identity_factory, mock_logging_service,
):
    """personal cred с RoleACL → blocked; без ACL → deleted."""
    owner_id = "usr_to_be_deleted"
    owner = identity_factory(
        user_id=owner_id,
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )

    # cred_with_acl
    c1 = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "with_acl", "service": "jira", "scope": "personal", "secret": "s1"},
    )
    assert c1.status_code == 201
    cred_with_acl_id = c1.json()["id"]
    acl = await client.post(
        f"{BASE}/credentials/{cred_with_acl_id}/acl",
        headers=auth_header(owner),
        json={"dept_id": "dep_a", "role_name": "reader", "can_read": True},
    )
    assert acl.status_code == 201

    # cred_no_acl
    c2 = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "no_acl", "service": "jira", "scope": "personal", "secret": "s2"},
    )
    assert c2.status_code == 201
    cred_no_acl_id = c2.json()["id"]

    # Lifecycle: user deleted.
    resp = await client.post(
        f"{BASE}/internal/lifecycle/user-deleted",
        headers=_internal_header(),
        json={"user_id": owner_id, "actor_id": "usr_admin_actor"},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["blocked_count"] == 1
    assert summary["deleted_count"] == 1
    assert summary["errors"] == []

    # cred_with_acl → blocked. Owner всё ещё видит, но получит 410 (если бы он
    # ещё был валидной identity; introspect его теперь не отдаст — но мы
    # имеем сейчас сохранённый owner-token в моке). Service_admin видит meta
    # без 410.
    admin = identity_factory(
        user_id="usr_svc_admin",
        department_id="dep_a",
        service_roles={"secret_service": ["admin"]},
    )
    get_blocked = await client.get(
        f"{BASE}/credentials/{cred_with_acl_id}", headers=auth_header(admin),
    )
    # service_admin может читать blocked для аудита → 200.
    assert get_blocked.status_code == 200
    assert get_blocked.json()["status"] == "blocked"

    # cred_no_acl — hard-deleted → 404.
    get_deleted = await client.get(
        f"{BASE}/credentials/{cred_no_acl_id}", headers=auth_header(admin),
    )
    assert get_deleted.status_code == 404

    actions = {e["action"] for e in mock_logging_service}
    assert "tokens.owner_user_deleted_block" in actions
    assert "tokens.delete" in actions


async def test_dept_deleted_as_owner_blocks_creds(
    client, identity_factory, mock_logging_service,
):
    """delete_dept на owner dep → все cred'ы dep'а → blocked."""
    dep_admin = identity_factory(
        user_id="usr_dep_admin",
        department_id="dep_x",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    create = await client.post(
        f"{BASE}/credentials", headers=auth_header(dep_admin),
        json={
            "name": "dept_cred",
            "service": "jira",
            "scope": "department",
            "secret": "dept-secret",
            "owner_dept_id": "dep_x",
        },
    )
    assert create.status_code == 201
    cred_id = create.json()["id"]

    resp = await client.post(
        f"{BASE}/internal/lifecycle/dept-deleted",
        headers=_internal_header(),
        json={"dept_id": "dep_x", "actor_id": "usr_account_admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["blocked_count"] == 1

    # service_admin читает blocked cred — статус blocked.
    admin = identity_factory(
        user_id="usr_svc_admin",
        department_id="dep_a",
        service_roles={"secret_service": ["admin"]},
    )
    get_blocked = await client.get(
        f"{BASE}/credentials/{cred_id}", headers=auth_header(admin),
    )
    assert get_blocked.status_code == 200
    body = get_blocked.json()
    assert body["status"] == "blocked"
    assert body["blocked_reason"] == "owner_dept_deleted"

    assert mock_logging_service.find_one("tokens.owner_dept_deleted_block") is not None


async def test_dept_deleted_as_recipient_cascades_grants(
    client, identity_factory, mock_logging_service,
):
    """delete_dept на recipient cross_dep — cascade grants + acls."""
    owner_admin = identity_factory(
        user_id="usr_owner_admin",
        department_id="dep_owner",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    create = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner_admin),
        json={
            "name": "x_dep_cred",
            "service": "git",
            "scope": "cross_department",
            "secret": "x",
            "owner_dept_id": "dep_owner",
        },
    )
    assert create.status_code == 201
    cred_id = create.json()["id"]

    grant = await client.post(
        f"{BASE}/credentials/{cred_id}/dept-grants",
        headers=auth_header(owner_admin),
        json={"recipient_dept_id": "dep_recipient"},
    )
    assert grant.status_code == 201

    recipient_admin = identity_factory(
        user_id="usr_recipient_admin",
        department_id="dep_recipient",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    acl = await client.post(
        f"{BASE}/credentials/{cred_id}/acl",
        headers=auth_header(recipient_admin),
        json={"dept_id": "dep_recipient", "role_name": "reader", "can_read": True},
    )
    assert acl.status_code == 201

    # delete dep_recipient.
    resp = await client.post(
        f"{BASE}/internal/lifecycle/dept-deleted",
        headers=_internal_header(),
        json={"dept_id": "dep_recipient", "actor_id": "usr_admin"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dept_grants_revoked"] >= 1
    assert body["role_acls_revoked"] >= 1

    assert mock_logging_service.find_one("tokens.dept_recipient_cascade") is not None


async def test_dept_service_access_revoked_cascade(
    client, identity_factory, mock_logging_service,
):
    """revoke department_service_access → cascade DeptGrant + RoleACL."""
    owner_admin = identity_factory(
        user_id="usr_owner_admin2",
        department_id="dep_owner2",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    create = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner_admin),
        json={
            "name": "revoke_target",
            "service": "git",
            "scope": "cross_department",
            "secret": "x",
            "owner_dept_id": "dep_owner2",
        },
    )
    cred_id = create.json()["id"]

    grant = await client.post(
        f"{BASE}/credentials/{cred_id}/dept-grants",
        headers=auth_header(owner_admin),
        json={"recipient_dept_id": "dep_revoke"},
    )
    assert grant.status_code == 201

    revoke_admin = identity_factory(
        user_id="usr_recipient_admin2",
        department_id="dep_revoke",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    acl = await client.post(
        f"{BASE}/credentials/{cred_id}/acl",
        headers=auth_header(revoke_admin),
        json={"dept_id": "dep_revoke", "role_name": "reader", "can_read": True},
    )
    assert acl.status_code == 201

    resp = await client.post(
        f"{BASE}/internal/lifecycle/dept-service-access-revoked",
        headers=_internal_header(),
        json={
            "dept_id": "dep_revoke",
            "service": "secret_service",
            "actor_id": "usr_account_admin",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dept_grants_revoked"] >= 1
    assert body["role_acls_revoked"] >= 1

    assert mock_logging_service.find_one("tokens.dept_revoke_cascade") is not None


async def test_internal_endpoint_requires_bearer(client):
    """`/internal/*` без bearer → 401."""
    resp = await client.post(
        f"{BASE}/internal/lifecycle/user-deleted",
        json={"user_id": "usr_x", "actor_id": "usr_y"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INTERNAL_AUTH_REQUIRED"
