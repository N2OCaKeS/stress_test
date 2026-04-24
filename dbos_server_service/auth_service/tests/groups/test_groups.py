"""Тесты: /api/auth/v1/groups — управление группами пользователей."""

GROUPS_URL = "/api/auth/v1/groups"
USERS_URL = "/api/auth/v1/users"


async def _create_group(client, token, name="test_group", display_name="Test Group"):
    return await client.post(GROUPS_URL, headers={"Authorization": f"Bearer {token}"},
                             json={"name": name, "display_name": display_name})


# ── Group CRUD ────────────────────────────────────────────────────────────────

async def test_admin_creates_group(client, admin_token):
    resp = await _create_group(client, admin_token)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "test_group"
    assert body["is_active"] is True


async def test_duplicate_group_name_returns_409(client, admin_token):
    await _create_group(client, admin_token, name="dup_group")
    resp = await _create_group(client, admin_token, name="dup_group")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "GROUP_ALREADY_EXISTS"


async def test_regular_user_cannot_create_group(client, user_a_token):
    resp = await _create_group(client, user_a_token, name="hack_group")
    assert resp.status_code == 403


async def test_admin_lists_groups(client, admin_token):
    await _create_group(client, admin_token, name="listed_group")
    resp = await client.get(GROUPS_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [g["name"] for g in resp.json()]
    assert "listed_group" in names


async def test_admin_updates_group(client, admin_token):
    group_id = (await _create_group(client, admin_token, name="to_update")).json()["id"]
    resp = await client.patch(f"{GROUPS_URL}/{group_id}",
                               headers={"Authorization": f"Bearer {admin_token}"},
                               json={"display_name": "Updated Name"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Updated Name"


async def test_admin_deletes_group(client, admin_token):
    group_id = (await _create_group(client, admin_token, name="to_delete_grp")).json()["id"]
    resp = await client.delete(f"{GROUPS_URL}/{group_id}",
                                headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


# ── Membership ────────────────────────────────────────────────────────────────

async def test_admin_adds_member(client, admin_token, user_a):
    group_id = (await _create_group(client, admin_token, name="members_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/members",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"user_id": user_a.id})
    assert resp.status_code == 201
    assert resp.json()["user_id"] == user_a.id


async def test_duplicate_membership_returns_409(client, admin_token, user_a):
    group_id = (await _create_group(client, admin_token, name="dup_member_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/members",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_id": user_a.id})
    resp = await client.post(f"{GROUPS_URL}/{group_id}/members",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"user_id": user_a.id})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "ALREADY_GROUP_MEMBER"


async def test_admin_removes_member(client, admin_token, user_a):
    group_id = (await _create_group(client, admin_token, name="remove_member_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/members",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_id": user_a.id})
    resp = await client.delete(f"{GROUPS_URL}/{group_id}/members/{user_a.id}",
                                headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


async def test_list_group_members(client, admin_token, user_a, user_b):
    group_id = (await _create_group(client, admin_token, name="list_members_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/members",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_id": user_a.id})
    resp = await client.get(f"{GROUPS_URL}/{group_id}/members",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    user_ids = [m["user_id"] for m in resp.json()]
    assert user_a.id in user_ids
    assert user_b.id not in user_ids


async def test_user_groups_endpoint(client, admin_token, user_a_token, user_a):
    group_id = (await _create_group(client, admin_token, name="user_grps_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/members",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_id": user_a.id})
    resp = await client.get(f"{USERS_URL}/{user_a.id}/groups",
                             headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200
    group_ids = [g["group_id"] for g in resp.json()]
    assert group_id in group_ids


async def test_dept_admin_can_add_own_dept_user(client, dept_admin_a_token, user_a, admin_token):
    group_id = (await _create_group(client, admin_token, name="dept_admin_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/members",
                              headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"user_id": user_a.id})
    assert resp.status_code == 201


async def test_dept_admin_cannot_add_other_dept_user(client, dept_admin_a_token, user_b, admin_token):
    group_id = (await _create_group(client, admin_token, name="dept_admin_cross_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/members",
                              headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"user_id": user_b.id})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


# ── Group service access ──────────────────────────────────────────────────────

async def test_admin_grants_service_to_group(client, admin_token, service_x):
    group_id = (await _create_group(client, admin_token, name="svc_access_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/services",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 201
    assert resp.json()["service_name"] == service_x.service_name
    assert resp.json()["is_active"] is True


async def test_duplicate_service_grant_returns_409(client, admin_token, service_x):
    group_id = (await _create_group(client, admin_token, name="dup_svc_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/services",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"service_name": service_x.service_name})
    resp = await client.post(f"{GROUPS_URL}/{group_id}/services",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "GROUP_SERVICE_ALREADY_GRANTED"


async def test_admin_revokes_service_from_group(client, admin_token, service_x):
    group_id = (await _create_group(client, admin_token, name="revoke_svc_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/services",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"service_name": service_x.service_name})
    resp = await client.delete(f"{GROUPS_URL}/{group_id}/services/{service_x.service_name}",
                                headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


# ── Group service roles ───────────────────────────────────────────────────────

async def test_admin_assigns_roles_to_group(client, admin_token, service_x):
    group_id = (await _create_group(client, admin_token, name="role_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/roles",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name, "roles": ["reader"]})
    assert resp.status_code == 201
    assert "reader" in resp.json()["roles"]


async def test_assign_invalid_role_to_group_returns_422(client, admin_token, service_x):
    group_id = (await _create_group(client, admin_token, name="bad_role_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/roles",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name, "roles": ["nonexistent"]})
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_SERVICE_ROLE"


async def test_group_roles_visible_in_list(client, admin_token, service_x):
    group_id = (await _create_group(client, admin_token, name="list_roles_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/roles",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"service_name": service_x.service_name, "roles": ["operator"]})
    resp = await client.get(f"{GROUPS_URL}/{group_id}/roles",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    service_names = [r["service_name"] for r in resp.json()]
    assert service_x.service_name in service_names


async def test_revoke_group_roles(client, admin_token, service_x):
    group_id = (await _create_group(client, admin_token, name="revoke_roles_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/roles",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"service_name": service_x.service_name, "roles": ["reader"]})
    resp = await client.delete(f"{GROUPS_URL}/{group_id}/roles/{service_x.service_name}",
                                headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
