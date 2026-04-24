"""Тесты: массовая выдача/отзыв ролей и initial_roles при создании пользователя."""

ROLES_URL = "/api/auth/v1/services/{service_name}/roles"
USERS_URL = "/api/auth/v1/users"


# ── Bulk assign ───────────────────────────────────────────────────────────────

async def test_bulk_assign_role(client, admin_token, user_a, user_b, service_x,
                                dept_a_with_service, dept_b, db):
    from tests.conftest import _grant_service
    await _grant_service(db, dept_b.id, service_x.service_name)
    url = f"{ROLES_URL.format(service_name=service_x.service_name)}/reader/assign"
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"user_ids": [user_a.id, user_b.id]})
    assert resp.status_code == 200


async def test_bulk_assign_nonexistent_role_returns_404(client, admin_token, user_a,
                                                         dept_a_with_service, service_x):
    url = f"{ROLES_URL.format(service_name=service_x.service_name)}/ghost_role/assign"
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"user_ids": [user_a.id]})
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SERVICE_ROLE_NOT_FOUND"


async def test_bulk_assign_user_dept_no_access_returns_403(client, admin_token, user_b, service_x):
    """user_b is in dept_b which has no access to service_x."""
    url = f"{ROLES_URL.format(service_name=service_x.service_name)}/reader/assign"
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"user_ids": [user_b.id]})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"


# ── Bulk revoke ───────────────────────────────────────────────────────────────

async def test_bulk_revoke_role(client, admin_token, user_a, service_x, dept_a_with_service):
    assign_url = f"{ROLES_URL.format(service_name=service_x.service_name)}/reader/assign"
    await client.post(assign_url, headers={"Authorization": f"Bearer {admin_token}"},
                       json={"user_ids": [user_a.id]})
    revoke_url = f"{ROLES_URL.format(service_name=service_x.service_name)}/reader/revoke"
    resp = await client.post(revoke_url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"user_ids": [user_a.id]})
    assert resp.status_code == 200


# ── Auto-revoke on role delete ────────────────────────────────────────────────

async def test_delete_role_auto_revokes_from_users(client, admin_token, user_a,
                                                    service_x, dept_a_with_service, db):
    from tests.conftest import _assign_role
    await _assign_role(db, user_a.id, service_x.service_name, "guest")
    del_resp = await client.delete(
        f"{ROLES_URL.format(service_name=service_x.service_name)}/guest",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert del_resp.status_code == 200
    from src.repositories.roles import RoleRepository
    role_repo = RoleRepository(db)
    roles = await role_repo.get_roles_by_service(user_a.id, service_x.service_name)
    assert "guest" not in roles


# ── initial_roles on user create ──────────────────────────────────────────────

async def test_create_user_with_initial_roles(client, admin_token, dept_a_with_service, service_x):
    resp = await client.post(USERS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={
                                  "username": "new_user_with_roles",
                                  "password": "Secure1234!",
                                  "department_id": dept_a_with_service.id,
                                  "initial_roles": [
                                      {"service_name": service_x.service_name, "roles": ["reader"]}
                                  ],
                              })
    assert resp.status_code == 201


async def test_create_user_with_invalid_initial_role_returns_422(
    client, admin_token, dept_a_with_service, service_x
):
    resp = await client.post(USERS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={
                                  "username": "bad_role_user",
                                  "password": "Secure1234!",
                                  "department_id": dept_a_with_service.id,
                                  "initial_roles": [
                                      {"service_name": service_x.service_name, "roles": ["nonexistent"]}
                                  ],
                              })
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_SERVICE_ROLE"


async def test_create_user_initial_role_service_not_in_dept_returns_403(
    client, admin_token, dept_b, service_x
):
    """service_x not granted to dept_b — initial_roles should fail."""
    resp = await client.post(USERS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={
                                  "username": "cross_dept_user",
                                  "password": "Secure1234!",
                                  "department_id": dept_b.id,
                                  "initial_roles": [
                                      {"service_name": service_x.service_name, "roles": ["reader"]}
                                  ],
                              })
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"
