"""Двухуровневый cross_department flow: DeptGrant → RoleACL → reveal → cascade.

README §«cross_department»:
  1. owner dep_admin создаёт DeptGrant(cred_id, recipient_dept_id).
  2. recipient dep_admin создаёт RoleACL(cred_id, recipient_dept_id, role, can_read).
  3. reader@recipient_dep → read + reveal ok.
  4. owner dep_admin revoke DeptGrant → cascade RoleACL → reader@recipient → 404.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import auth_header

BASE = "/api/secret/v1"

pytestmark = pytest.mark.asyncio


async def test_cross_dep_full_flow_with_cascade_revoke(
    client, identity_factory, mock_logging_service,
):
    owner_admin = identity_factory(
        user_id="usr_owner_admin",
        department_id="dep_b",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )

    # 1. Owner создаёт cross_department cred.
    create = await client.post(
        f"{BASE}/credentials",
        headers=auth_header(owner_admin),
        json={
            "name": "git_mirror",
            "service": "git",
            "scope": "cross_department",
            "secret": "mirror-secret",
            "owner_dept_id": "dep_b",
        },
    )
    assert create.status_code == 201, create.text
    cred_id = create.json()["id"]

    # 2. Reader из dep_a без DeptGrant — 404.
    reader_a = identity_factory(
        user_id="usr_reader_a",
        department_id="dep_a",
        service_roles={"secret_service": ["reader"]},
    )
    pre = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(reader_a))
    assert pre.status_code == 404

    # 3. Owner dep_admin выдаёт DeptGrant для dep_a.
    grant_resp = await client.post(
        f"{BASE}/credentials/{cred_id}/dept-grants",
        headers=auth_header(owner_admin),
        json={"recipient_dept_id": "dep_a"},
    )
    assert grant_resp.status_code == 201, grant_resp.text
    grant_id = grant_resp.json()["id"]

    # 4. Без RoleACL читать reader_a всё ещё не может — нужен ACL внутри его dep'а.
    no_acl = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(reader_a))
    # check_access без can_read → 403 CREDENTIAL_ACCESS_DENIED.
    assert no_acl.status_code == 403
    assert no_acl.json()["error_code"] == "CREDENTIAL_ACCESS_DENIED"

    # 5. recipient dep_admin создаёт RoleACL(dep_a, reader, can_read=True).
    recipient_admin = identity_factory(
        user_id="usr_recipient_admin",
        department_id="dep_a",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    acl_resp = await client.post(
        f"{BASE}/credentials/{cred_id}/acl",
        headers=auth_header(recipient_admin),
        json={
            "dept_id": "dep_a",
            "role_name": "reader",
            "can_read": True,
        },
    )
    assert acl_resp.status_code == 201, acl_resp.text

    # 6. Reader_a теперь может GET + reveal.
    read_resp = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(reader_a))
    assert read_resp.status_code == 200

    reveal_resp = await client.post(
        f"{BASE}/credentials/{cred_id}/reveal", headers=auth_header(reader_a),
    )
    assert reveal_resp.status_code == 200

    # 7. Owner dep_admin revoke DeptGrant → cascade RoleACL.
    revoke = await client.delete(
        f"{BASE}/credentials/{cred_id}/dept-grants/{grant_id}",
        headers=auth_header(owner_admin),
    )
    assert revoke.status_code == 200

    # 8. Reader_a больше не видит → 404 (нет DeptGrant'а — visibility miss).
    after = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(reader_a))
    assert after.status_code == 404

    reveal_after = await client.post(
        f"{BASE}/credentials/{cred_id}/reveal", headers=auth_header(reader_a),
    )
    assert reveal_after.status_code == 404

    # 9. Audit: dept_grant_added + role_acl_added + dept_grant_revoked + cascade.
    actions = {e["action"] for e in mock_logging_service}
    assert "tokens.dept_grant_added" in actions
    assert "tokens.role_acl_added" in actions
    assert "tokens.dept_grant_revoked" in actions
    # cascade — эмитится только если RoleACL'и реально удалились (>0).
    assert "tokens.dept_revoke_cascade" in actions
