"""Тесты: массовая выдача/отзыв ролей и initial_roles при создании пользователя."""

ROLES_URL = (
    "/api/auth/v1/departments/{department_id}/services/{service_name}/roles"
)
USERS_URL = "/api/auth/v1/users"


def _roles_url(department_id, service_name):
    return ROLES_URL.format(department_id=department_id, service_name=service_name)


# ── Bulk assign ───────────────────────────────────────────────────────────────

async def test_bulk_assign_role(client, admin_token, user_a, dept_a_with_service, service_x):
    url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/assign"
    resp = await client.post(
        url, headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_a.id]},
    )
    assert resp.status_code == 200


async def test_bulk_assign_nonexistent_role_returns_404(
    client, admin_token, user_a, dept_a_with_service, service_x,
):
    url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/ghost_role/assign"
    resp = await client.post(
        url, headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_a.id]},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SERVICE_ROLE_NOT_FOUND"


async def test_bulk_assign_cross_dept_user_returns_403(
    client, admin_token, user_b, dept_a_with_service, service_x,
):
    """A user from a different department cannot be assigned a role in dept_a."""
    url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/assign"
    resp = await client.post(
        url, headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_b.id]},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "USER_DEPARTMENT_MISMATCH"


# ── Bulk revoke ───────────────────────────────────────────────────────────────

async def test_bulk_revoke_role(client, admin_token, user_a, service_x, dept_a_with_service):
    assign_url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/assign"
    await client.post(
        assign_url, headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_a.id]},
    )
    revoke_url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/revoke"
    resp = await client.post(
        revoke_url, headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_a.id]},
    )
    assert resp.status_code == 200


# ── Auto-revoke on role delete ────────────────────────────────────────────────

async def test_delete_role_auto_revokes_from_users(
    client, admin_token, user_a, service_x, dept_a_with_service, db,
):
    from tests.conftest import _assign_role
    await _assign_role(db, user_a.id, service_x.service_name, "guest")
    del_resp = await client.delete(
        f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/guest",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert del_resp.status_code == 200
    from src.repositories.roles import RoleRepository
    role_repo = RoleRepository(db)
    roles = await role_repo.get_roles_by_service(user_a.id, service_x.service_name)
    assert "guest" not in roles


# ── initial_roles on user create ──────────────────────────────────────────────

async def test_create_user_with_initial_roles(
    client, admin_token, dept_a_with_service, service_x,
):
    resp = await client.post(
        USERS_URL, headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "new_user_with_roles",
            "password": "Secure1234!",
            "department_id": dept_a_with_service.id,
            "initial_roles": [
                {"service_name": service_x.service_name, "roles": ["reader"]}
            ],
        },
    )
    assert resp.status_code == 201


async def test_create_user_with_invalid_initial_role_returns_422(
    client, admin_token, dept_a_with_service, service_x,
):
    resp = await client.post(
        USERS_URL, headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "bad_role_user",
            "password": "Secure1234!",
            "department_id": dept_a_with_service.id,
            "initial_roles": [
                {"service_name": service_x.service_name, "roles": ["nonexistent"]}
            ],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_SERVICE_ROLE"


async def test_create_user_initial_role_service_not_in_dept_returns_403(
    client, admin_token, dept_b, service_x,
):
    """service_x not granted to dept_b — initial_roles should fail."""
    resp = await client.post(
        USERS_URL, headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "cross_dept_user",
            "password": "Secure1234!",
            "department_id": dept_b.id,
            "initial_roles": [
                {"service_name": service_x.service_name, "roles": ["reader"]}
            ],
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"


# ── Identity-cache invalidation after bulk role ops ───────────────────────────

async def test_bulk_assign_invalidates_identity_cache(
    client, admin_token, user_a, dept_a_with_service, service_x, monkeypatch,
):
    """bulk_assign должен сбросить identity-кэш каждого затронутого юзера.
    Иначе live-роль появляется со следующим cache-miss (до TTL=5s окно).
    """
    invalidated: list[str] = []
    from src.dependencies import auth as deps_auth_mod
    original = deps_auth_mod.invalidate_identity_cache_for_user

    def spy(user_id: str) -> int:
        invalidated.append(user_id)
        return original(user_id)

    monkeypatch.setattr(deps_auth_mod, "invalidate_identity_cache_for_user", spy)

    url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/assign"
    resp = await client.post(
        url, headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_a.id]},
    )
    assert resp.status_code == 200
    assert user_a.id in invalidated


async def test_bulk_revoke_invalidates_identity_cache(
    client, admin_token, user_a, service_x, dept_a_with_service, monkeypatch,
):
    """bulk_revoke должен сбросить кэш — иначе отозванная роль остаётся в
    cached identity (нарушение контракта §9: revoke вступает в силу немедленно).
    """
    assign_url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/assign"
    await client.post(
        assign_url, headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_a.id]},
    )

    invalidated: list[str] = []
    from src.dependencies import auth as deps_auth_mod
    original = deps_auth_mod.invalidate_identity_cache_for_user

    def spy(user_id: str) -> int:
        invalidated.append(user_id)
        return original(user_id)

    monkeypatch.setattr(deps_auth_mod, "invalidate_identity_cache_for_user", spy)

    revoke_url = f"{_roles_url(dept_a_with_service.id, service_x.service_name)}/reader/revoke"
    resp = await client.post(
        revoke_url, headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_ids": [user_a.id]},
    )
    assert resp.status_code == 200
    assert user_a.id in invalidated
