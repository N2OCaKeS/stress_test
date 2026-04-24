"""Тесты: /api/auth/v1/services/{service_name}/roles — управление ролями сервиса."""

ROLES_URL = "/api/auth/v1/services/{service_name}/roles"
SERVICES_URL = "/api/auth/v1/services"
USERS_ROLES_URL = "/api/auth/v1/users/{user_id}/roles"


# ── List roles ────────────────────────────────────────────────────────────────

async def test_admin_lists_roles(client, admin_token, service_x):
    resp = await client.get(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    role_names = [r["role_name"] for r in resp.json()]
    assert "admin" in role_names


async def test_service_not_found_returns_404(client, admin_token):
    resp = await client.get(
        ROLES_URL.format(service_name="nonexistent_svc"),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SERVICE_NOT_FOUND"


async def test_unauthorized_user_cannot_list_roles(client, user_a_token, service_x):
    resp = await client.get(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_ROLE_MGMT_FORBIDDEN"


# ── Create role ───────────────────────────────────────────────────────────────

async def test_admin_creates_role(client, admin_token, service_x):
    resp = await client.post(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role_name": "custom_role", "display_name": "Custom Role", "description": "A custom role"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["role_name"] == "custom_role"
    assert body["display_name"] == "Custom Role"
    assert body["service_name"] == service_x.service_name


async def test_duplicate_role_returns_409(client, admin_token, service_x):
    await client.post(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role_name": "dup_role", "display_name": "Dup"},
    )
    resp = await client.post(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role_name": "dup_role", "display_name": "Dup"},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "SERVICE_ROLE_ALREADY_EXISTS"


async def test_regular_user_cannot_create_role(client, user_a_token, service_x):
    resp = await client.post(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"role_name": "hack_role", "display_name": "Hack"},
    )
    assert resp.status_code == 403


async def test_service_admin_can_create_role(client, admin_token, user_a_token, service_x, user_a,
                                              dept_a_with_service, db):
    """Пользователь с ролью 'admin' на сервисе может создавать роли."""
    from tests.conftest import _assign_role
    await _assign_role(db, user_a.id, service_x.service_name, "admin")
    # переаутентифицируемся чтобы получить токен с обновлённой ролью
    from tests.conftest import _login
    token = await _login(client, "t_user_a", "User1234!")
    resp = await client.post(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {token}"},
        json={"role_name": "svc_admin_role", "display_name": "SvcAdmin Role"},
    )
    assert resp.status_code == 201


# ── Update role ───────────────────────────────────────────────────────────────

async def test_admin_updates_role(client, admin_token, service_x):
    await client.post(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role_name": "to_update", "display_name": "Old Name"},
    )
    resp = await client.patch(
        f"{ROLES_URL.format(service_name=service_x.service_name)}/to_update",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"display_name": "New Name", "description": "Updated desc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "New Name"
    assert body["description"] == "Updated desc"


async def test_update_nonexistent_role_returns_404(client, admin_token, service_x):
    resp = await client.patch(
        f"{ROLES_URL.format(service_name=service_x.service_name)}/ghost_role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"display_name": "X"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SERVICE_ROLE_NOT_FOUND"


# ── Delete role ───────────────────────────────────────────────────────────────

async def test_admin_deletes_role(client, admin_token, service_x):
    await client.post(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role_name": "to_delete", "display_name": "To Delete"},
    )
    resp = await client.delete(
        f"{ROLES_URL.format(service_name=service_x.service_name)}/to_delete",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


async def test_delete_nonexistent_role_returns_404(client, admin_token, service_x):
    resp = await client.delete(
        f"{ROLES_URL.format(service_name=service_x.service_name)}/ghost_role",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


async def test_deleted_role_not_in_list(client, admin_token, service_x):
    await client.post(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role_name": "temp_role", "display_name": "Temp"},
    )
    await client.delete(
        f"{ROLES_URL.format(service_name=service_x.service_name)}/temp_role",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    resp = await client.get(
        ROLES_URL.format(service_name=service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    role_names = [r["role_name"] for r in resp.json()]
    assert "temp_role" not in role_names


# ── Auto-creation on service create ──────────────────────────────────────────

async def test_admin_role_auto_created_with_service(client, admin_token):
    resp = await client.post(
        SERVICES_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": "auto_role_svc", "display_name": "Auto Role Svc"},
    )
    assert resp.status_code == 201
    roles_resp = await client.get(
        ROLES_URL.format(service_name="auto_role_svc"),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    role_names = [r["role_name"] for r in roles_resp.json()]
    assert "admin" in role_names


# ── Role validation on assign ─────────────────────────────────────────────────

async def test_assign_undefined_role_returns_422(client, admin_token, user_a, dept_a_with_service, service_x):
    resp = await client.post(
        USERS_ROLES_URL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["nonexistent_role"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_SERVICE_ROLE"
