"""Тесты: /api/auth/v1/departments/{department_id}/services/{service_name}/roles
— per-department управление ролями сервиса."""

ROLES_URL = "/api/auth/v1/departments/{department_id}/services/{service_name}/roles"
SERVICES_URL = "/api/auth/v1/services"
USERS_ROLES_URL = "/api/auth/v1/users/{user_id}/roles"


def _roles_url(department_id, service_name):
    return ROLES_URL.format(department_id=department_id, service_name=service_name)


# ── List roles ────────────────────────────────────────────────────────────────

async def test_admin_lists_roles(client, admin_token, dept_a_with_service, service_x):
    resp = await client.get(
        _roles_url(dept_a_with_service.id, service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    role_names = [r["role_name"] for r in resp.json()]
    assert "admin" in role_names
    # The seeded `admin` role is system-managed.
    admin_row = next(r for r in resp.json() if r["role_name"] == "admin")
    assert admin_row["is_system"] is True


async def test_service_not_found_returns_404(client, admin_token, dept_a):
    resp = await client.get(
        _roles_url(dept_a.id, "nonexistent_svc"),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SERVICE_NOT_FOUND"


async def test_department_not_found_returns_404(client, admin_token, service_x):
    resp = await client.get(
        _roles_url("dep_ghost", service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "DEPARTMENT_NOT_FOUND"


async def test_dept_without_service_access_returns_422(client, admin_token, dept_a, service_x):
    """Service exists but department has no access — cannot manage its roles."""
    resp = await client.get(
        _roles_url(dept_a.id, service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "SERVICE_NOT_GRANTED_FOR_DEPARTMENT"


async def test_unauthorized_user_cannot_list_roles(client, user_a_token, dept_a_with_service, service_x):
    resp = await client.get(
        _roles_url(dept_a_with_service.id, service_x.service_name),
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_ROLE_MGMT_FORBIDDEN"


async def test_dept_admin_cannot_manage_other_dept(
    client, dept_admin_a_token, dept_b, service_x, db
):
    """department_admin scoped to dept_a cannot touch roles in dept_b."""
    from tests.conftest import _grant_service
    await _grant_service(db, dept_b.id, service_x.service_name)
    resp = await client.get(
        _roles_url(dept_b.id, service_x.service_name),
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


# ── Create role ───────────────────────────────────────────────────────────────

async def test_admin_creates_role(client, admin_token, dept_a_with_service, service_x):
    resp = await client.post(
        _roles_url(dept_a_with_service.id, service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role_name": "custom_role", "description": "A custom role"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["role_name"] == "custom_role"
    assert body["service_name"] == service_x.service_name
    assert body["department_id"] == dept_a_with_service.id
    assert body["is_system"] is False


async def test_duplicate_role_returns_409(client, admin_token, dept_a_with_service, service_x):
    url = _roles_url(dept_a_with_service.id, service_x.service_name)
    await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                      json={"role_name": "dup_role"})
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"role_name": "dup_role"})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "SERVICE_ROLE_ALREADY_EXISTS"


async def test_same_role_name_allowed_in_different_dept(
    client, admin_token, dept_a_with_service, dept_b, service_x, db,
):
    """A role name can exist independently in each department's catalog."""
    from tests.conftest import _grant_service
    await _grant_service(db, dept_b.id, service_x.service_name)

    body = {"role_name": "shared_name"}
    a = await client.post(
        _roles_url(dept_a_with_service.id, service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"}, json=body,
    )
    b = await client.post(
        _roles_url(dept_b.id, service_x.service_name),
        headers={"Authorization": f"Bearer {admin_token}"}, json=body,
    )
    assert a.status_code == 201
    assert b.status_code == 201
    assert a.json()["department_id"] != b.json()["department_id"]


async def test_regular_user_cannot_create_role(client, user_a_token, dept_a_with_service, service_x):
    resp = await client.post(
        _roles_url(dept_a_with_service.id, service_x.service_name),
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"role_name": "hack_role"},
    )
    assert resp.status_code == 403


async def test_service_admin_can_create_role_in_own_dept(
    client, user_a, dept_a_with_service, service_x, db,
):
    """User holding the `admin` service role can manage role catalog
    for that service inside their own department."""
    from tests.conftest import _assign_role, _login
    await _assign_role(db, user_a.id, service_x.service_name, "admin")
    token = await _login(client, "t_user_a", "User12345678!")

    resp = await client.post(
        _roles_url(dept_a_with_service.id, service_x.service_name),
        headers={"Authorization": f"Bearer {token}"},
        json={"role_name": "svc_admin_role"},
    )
    assert resp.status_code == 201


async def test_service_admin_cannot_create_role_in_other_dept(
    client, user_a, dept_a_with_service, dept_b, service_x, db,
):
    from tests.conftest import _assign_role, _grant_service, _login
    await _grant_service(db, dept_b.id, service_x.service_name)
    await _assign_role(db, user_a.id, service_x.service_name, "admin")
    token = await _login(client, "t_user_a", "User12345678!")

    resp = await client.post(
        _roles_url(dept_b.id, service_x.service_name),
        headers={"Authorization": f"Bearer {token}"},
        json={"role_name": "leak_role"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"


# ── Update role ───────────────────────────────────────────────────────────────

async def test_admin_updates_role(client, admin_token, dept_a_with_service, service_x):
    url = _roles_url(dept_a_with_service.id, service_x.service_name)
    await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                      json={"role_name": "to_update"})
    resp = await client.patch(
        f"{url}/to_update",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"description": "Updated desc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "Updated desc"


async def test_update_nonexistent_role_returns_404(client, admin_token, dept_a_with_service, service_x):
    resp = await client.patch(
        f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/ghost_role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"description": "X"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SERVICE_ROLE_NOT_FOUND"


async def test_system_admin_role_cannot_be_updated(client, admin_token, dept_a_with_service, service_x):
    resp = await client.patch(
        f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"description": "Should not work"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_ROLE_SYSTEM_LOCKED"


# ── Delete role ───────────────────────────────────────────────────────────────

async def test_admin_deletes_role(client, admin_token, dept_a_with_service, service_x):
    url = _roles_url(dept_a_with_service.id, service_x.service_name)
    await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                      json={"role_name": "to_delete"})
    resp = await client.delete(
        f"{url}/to_delete",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


async def test_delete_nonexistent_role_returns_404(client, admin_token, dept_a_with_service, service_x):
    resp = await client.delete(
        f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/ghost_role",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


async def test_deleted_role_not_in_list(client, admin_token, dept_a_with_service, service_x):
    url = _roles_url(dept_a_with_service.id, service_x.service_name)
    await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                      json={"role_name": "temp_role"})
    await client.delete(f"{url}/temp_role",
                        headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.get(url, headers={"Authorization": f"Bearer {admin_token}"})
    role_names = [r["role_name"] for r in resp.json()]
    assert "temp_role" not in role_names


async def test_system_admin_role_cannot_be_deleted(client, admin_token, dept_a_with_service, service_x):
    resp = await client.delete(
        f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/admin",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_ROLE_SYSTEM_LOCKED"


async def test_delete_role_invalidates_bot_identity_cache(
    client, admin_token, dept_a_with_service, service_x, monkeypatch,
):
    """После delete_role identity-кэш ботов с этой ролью должен сброситься
    оптом — иначе боты держат старые права до TTL."""
    invalidated: list[str] = []

    def _spy(actor_id):
        invalidated.append(actor_id)

    monkeypatch.setattr(
        "src.services.service_role_service._invalidate_identity_cache", _spy
    )

    url = _roles_url(dept_a_with_service.id, service_x.service_name)
    create_resp = await client.post(
        url,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role_name": "bot_role"},
    )
    assert create_resp.status_code == 201

    bot_resp = await client.post(
        "/api/auth/v1/bots",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "role_holder_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name],
        },
    )
    assert bot_resp.status_code == 201
    bot_id = bot_resp.json()["bot_id"]

    assign_resp = await client.post(
        f"/api/auth/v1/bots/{bot_id}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["bot_role"]},
    )
    assert assign_resp.status_code == 201

    invalidated.clear()

    del_resp = await client.delete(
        f"{url}/bot_role",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert del_resp.status_code == 200
    assert bot_id in invalidated, (
        f"identity-cache бота с прямой ролью должен быть сброшен после delete_role, got {invalidated}"
    )


# ── Auto-seed system admin on department grant ───────────────────────────────

async def test_admin_role_seeded_on_department_service_grant(
    client, admin_token, dept_a, db,
):
    """Granting a department access to a service via the API seeds an immutable
    `admin` role for that (department, service) pair."""
    # Create the service via API
    create_svc = await client.post(
        SERVICES_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": "auto_role_svc"},
    )
    assert create_svc.status_code == 201

    grant = await client.post(
        f"/api/auth/v1/departments/{dept_a.id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": "auto_role_svc"},
    )
    assert grant.status_code == 201

    roles_resp = await client.get(
        _roles_url(dept_a.id, "auto_role_svc"),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    role_names = [r["role_name"] for r in roles_resp.json()]
    assert "admin" in role_names
    admin_row = next(r for r in roles_resp.json() if r["role_name"] == "admin")
    assert admin_row["is_system"] is True


# ── Role validation on assign ─────────────────────────────────────────────────

async def test_assign_undefined_role_returns_422(
    client, admin_token, user_a, dept_a_with_service, service_x,
):
    resp = await client.post(
        USERS_ROLES_URL.format(user_id=user_a.id),
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["nonexistent_role"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_SERVICE_ROLE"
