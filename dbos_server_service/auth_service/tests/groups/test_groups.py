"""Тесты: /api/auth/v1/groups — управление группами пользователей (per-department)."""

GROUPS_URL = "/api/auth/v1/groups"
USERS_URL = "/api/auth/v1/users"


async def _create_group(client, token, department_id, name="test_group", display_name="Test Group"):
    return await client.post(
        GROUPS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"department_id": department_id, "name": name, "display_name": display_name},
    )


# ── Group CRUD ────────────────────────────────────────────────────────────────

async def test_admin_creates_group(client, admin_token, dept_a):
    resp = await _create_group(client, admin_token, dept_a.id)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "test_group"
    assert body["department_id"] == dept_a.id
    assert body["is_active"] is True


async def test_duplicate_group_name_in_same_dept_returns_409(client, admin_token, dept_a):
    await _create_group(client, admin_token, dept_a.id, name="dup_group")
    resp = await _create_group(client, admin_token, dept_a.id, name="dup_group")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "GROUP_ALREADY_EXISTS"


async def test_same_group_name_allowed_in_different_dept(client, admin_token, dept_a, dept_b):
    a = await _create_group(client, admin_token, dept_a.id, name="shared_name")
    b = await _create_group(client, admin_token, dept_b.id, name="shared_name")
    assert a.status_code == 201
    assert b.status_code == 201


async def test_regular_user_cannot_create_group(client, user_a_token, dept_a):
    resp = await _create_group(client, user_a_token, dept_a.id, name="hack_group")
    assert resp.status_code == 403


async def test_dept_admin_cannot_create_group_in_other_dept(
    client, dept_admin_a_token, dept_b,
):
    resp = await _create_group(client, dept_admin_a_token, dept_b.id, name="cross_dept_grp")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_FORBIDDEN"


async def test_admin_lists_groups(client, admin_token, dept_a):
    await _create_group(client, admin_token, dept_a.id, name="listed_group")
    resp = await client.get(GROUPS_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [g["name"] for g in resp.json()]
    assert "listed_group" in names


async def test_admin_updates_group(client, admin_token, dept_a):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="to_update")).json()["id"]
    resp = await client.patch(
        f"{GROUPS_URL}/{group_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"display_name": "Updated Name"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Updated Name"


async def test_admin_deletes_group(client, admin_token, dept_a):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="to_delete_grp")).json()["id"]
    resp = await client.delete(f"{GROUPS_URL}/{group_id}",
                                headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


# ── Membership ────────────────────────────────────────────────────────────────

async def test_admin_adds_member(client, admin_token, dept_a, user_a):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="members_grp")).json()["id"]
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_a.id},
    )
    assert resp.status_code == 201
    assert resp.json()["user_id"] == user_a.id


async def test_add_member_from_other_dept_returns_403(
    client, admin_token, dept_a, user_b,
):
    """Group is in dept_a, user_b is in dept_b — cannot mix."""
    group_id = (await _create_group(client, admin_token, dept_a.id, name="mix_grp")).json()["id"]
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_b.id},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "GROUP_DEPARTMENT_MISMATCH"


async def test_duplicate_membership_returns_409(client, admin_token, dept_a, user_a):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="dup_member_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/members",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_id": user_a.id})
    resp = await client.post(f"{GROUPS_URL}/{group_id}/members",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"user_id": user_a.id})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "ALREADY_GROUP_MEMBER"


async def test_admin_removes_member(client, admin_token, dept_a, user_a):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="remove_member_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/members",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_id": user_a.id})
    resp = await client.delete(f"{GROUPS_URL}/{group_id}/members/{user_a.id}",
                                headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


async def test_list_group_members(client, admin_token, dept_a, user_a, user_b):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="list_members_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/members",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_id": user_a.id})
    resp = await client.get(f"{GROUPS_URL}/{group_id}/members",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    user_ids = [m["user_id"] for m in resp.json()]
    assert user_a.id in user_ids
    assert user_b.id not in user_ids


async def test_user_groups_endpoint(client, admin_token, dept_a, user_a_token, user_a):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="user_grps_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/members",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_id": user_a.id})
    resp = await client.get(f"{USERS_URL}/{user_a.id}/groups",
                             headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200
    group_ids = [g["group_id"] for g in resp.json()]
    assert group_id in group_ids


async def test_dept_admin_can_add_own_dept_user(client, dept_admin_a_token, dept_a, user_a, admin_token):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="dept_admin_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/members",
                              headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"user_id": user_a.id})
    assert resp.status_code == 201


async def test_dept_admin_cannot_add_other_dept_user(
    client, dept_admin_a_token, dept_b, user_b, admin_token,
):
    """A dept_b group + a dept_b user — but dept_admin_a is scoped to dept_a."""
    group_id = (await _create_group(client, admin_token, dept_b.id, name="cross_admin_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/members",
                              headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"user_id": user_b.id})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


# ── Group service access ──────────────────────────────────────────────────────

async def test_admin_grants_service_to_group(client, admin_token, dept_a, service_x):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="svc_access_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/services",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 201
    assert resp.json()["service_name"] == service_x.service_name
    assert resp.json()["is_active"] is True


async def test_duplicate_service_grant_returns_409(client, admin_token, dept_a, service_x):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="dup_svc_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/services",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"service_name": service_x.service_name})
    resp = await client.post(f"{GROUPS_URL}/{group_id}/services",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "GROUP_SERVICE_ALREADY_GRANTED"


async def test_admin_revokes_service_from_group(client, admin_token, dept_a, service_x):
    group_id = (await _create_group(client, admin_token, dept_a.id, name="revoke_svc_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/services",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"service_name": service_x.service_name})
    resp = await client.delete(f"{GROUPS_URL}/{group_id}/services/{service_x.service_name}",
                                headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


# ── Group service roles ───────────────────────────────────────────────────────

async def test_admin_assigns_roles_to_group(client, admin_token, dept_a_with_service, service_x):
    group_id = (await _create_group(client, admin_token, dept_a_with_service.id, name="role_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/roles",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name, "roles": ["reader"]})
    assert resp.status_code == 201
    assert "reader" in resp.json()["roles"]


async def test_assign_invalid_role_to_group_returns_422(client, admin_token, dept_a_with_service, service_x):
    group_id = (await _create_group(client, admin_token, dept_a_with_service.id, name="bad_role_grp")).json()["id"]
    resp = await client.post(f"{GROUPS_URL}/{group_id}/roles",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name, "roles": ["nonexistent"]})
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_SERVICE_ROLE"


async def test_group_roles_visible_in_list(client, admin_token, dept_a_with_service, service_x):
    group_id = (await _create_group(client, admin_token, dept_a_with_service.id, name="list_roles_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/roles",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"service_name": service_x.service_name, "roles": ["operator"]})
    resp = await client.get(f"{GROUPS_URL}/{group_id}/roles",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    service_names = [r["service_name"] for r in resp.json()]
    assert service_x.service_name in service_names


async def test_revoke_group_roles(client, admin_token, dept_a_with_service, service_x):
    group_id = (await _create_group(client, admin_token, dept_a_with_service.id, name="revoke_roles_grp")).json()["id"]
    await client.post(f"{GROUPS_URL}/{group_id}/roles",
                       headers={"Authorization": f"Bearer {admin_token}"},
                       json={"service_name": service_x.service_name, "roles": ["reader"]})
    resp = await client.delete(f"{GROUPS_URL}/{group_id}/roles/{service_x.service_name}",
                                headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


# ── list_user_groups cross-department guard ──────────────────────────────────
# `GET /users/{user_id}/groups` used to let a department_admin from dept A read
# group memberships of any user in any other department, leaking group names
# (potentially sensitive — `prod_access`, `security_team`).  The service-level
# guard now mirrors `user_service.list_users_by_department`: dept_admin is
# pinned to their own department, account_admin keeps cross-dept visibility,
# regular users keep self-only visibility.


async def test_dept_admin_cannot_view_other_dept_user_groups(
    client, admin_token, dept_admin_a_token, dept_b, user_b,
):
    """dept_admin_a (dept_a) → user_b (dept_b) groups must be 403, not a leak."""
    # Seed a group + membership in dept_b so there's actually something to leak.
    group_id = (
        await _create_group(client, admin_token, dept_b.id, name="b_secret_grp")
    ).json()["id"]
    await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_b.id},
    )

    resp = await client.get(
        f"{USERS_URL}/{user_b.id}/groups",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error_code"] == "DEPARTMENT_ACCESS_DENIED"
    # Sanity: nothing about the actual group leaked into the error response.
    assert "b_secret_grp" not in resp.text


async def test_dept_admin_can_view_own_dept_user_groups(
    client, admin_token, dept_admin_a_token, dept_a, user_a,
):
    """dept_admin_a may still inspect users inside their own department (regression)."""
    group_id = (
        await _create_group(client, admin_token, dept_a.id, name="a_visible_grp")
    ).json()["id"]
    await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_a.id},
    )

    resp = await client.get(
        f"{USERS_URL}/{user_a.id}/groups",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 200
    group_ids = [g["group_id"] for g in resp.json()]
    assert group_id in group_ids


async def test_account_admin_can_view_any_user_groups(
    client, admin_token, dept_b, user_b,
):
    """account_admin keeps cross-department visibility (regression)."""
    group_id = (
        await _create_group(client, admin_token, dept_b.id, name="a_admin_visible_grp")
    ).json()["id"]
    await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_b.id},
    )

    resp = await client.get(
        f"{USERS_URL}/{user_b.id}/groups",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    group_ids = [g["group_id"] for g in resp.json()]
    assert group_id in group_ids


async def test_regular_user_cannot_view_other_user_groups(
    client, user_a_token, user_b,
):
    """Regression: regular users still get ROLE_REQUIRED on other-user inspect."""
    resp = await client.get(
        f"{USERS_URL}/{user_b.id}/groups",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ROLE_REQUIRED"


async def test_dept_admin_view_nonexistent_user_groups_returns_404(
    client, dept_admin_a_token,
):
    """If target user does not exist, dept_admin gets USER_NOT_FOUND (not the
    cross-dept guard) — verifies guard ordering is correct and doesn't leak
    membership of `usr_*` IDs via 403-vs-404 oracle.
    """
    resp = await client.get(
        f"{USERS_URL}/usr_ghost_does_not_exist/groups",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "USER_NOT_FOUND"


# ── Identity-cache invalidation after membership change ─────────────────────

async def test_add_member_invalidates_identity_cache(
    client, admin_token, dept_a, user_a, monkeypatch,
):
    """Добавление юзера в группу меняет его эффективные права (group-roles).
    Identity-кэш юзера должен быть сброшен — иначе live-роль появится только
    после TTL.
    """
    invalidated: list[str] = []
    from src.dependencies import auth as deps_auth_mod
    original = deps_auth_mod.invalidate_identity_cache_for_user

    def spy(user_id: str) -> int:
        invalidated.append(user_id)
        return original(user_id)

    monkeypatch.setattr(deps_auth_mod, "invalidate_identity_cache_for_user", spy)

    group_id = (await _create_group(
        client, admin_token, dept_a.id, name="cache_invalidate_add_grp",
    )).json()["id"]
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_a.id},
    )
    assert resp.status_code == 201
    assert user_a.id in invalidated


async def test_remove_member_invalidates_identity_cache(
    client, admin_token, dept_a, user_a, monkeypatch,
):
    """Удаление юзера из группы снимает с него group-roles. Cache должен быть
    сброшен немедленно — иначе revoke не вступает в силу до TTL.
    """
    group_id = (await _create_group(
        client, admin_token, dept_a.id, name="cache_invalidate_remove_grp",
    )).json()["id"]
    await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_a.id},
    )

    invalidated: list[str] = []
    from src.dependencies import auth as deps_auth_mod
    original = deps_auth_mod.invalidate_identity_cache_for_user

    def spy(user_id: str) -> int:
        invalidated.append(user_id)
        return original(user_id)

    monkeypatch.setattr(deps_auth_mod, "invalidate_identity_cache_for_user", spy)

    resp = await client.delete(
        f"{GROUPS_URL}/{group_id}/members/{user_a.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert user_a.id in invalidated
