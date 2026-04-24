"""Тесты: POST /api/auth/v1/users/{id}/roles — назначение ролей пользователям."""

URL_TPL = "/api/auth/v1/users/{user_id}/roles"


async def test_admin_assigns_role(client, admin_token, user_a, dept_a_with_service, service_x):
    url = URL_TPL.format(user_id=user_a.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "service_name": service_x.service_name, "roles": ["operator"],
    })
    assert resp.status_code == 200


async def test_dept_admin_assigns_role_in_own_dept(client, dept_admin_a_token, user_a, dept_a_with_service, service_x):
    url = URL_TPL.format(user_id=user_a.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {dept_admin_a_token}"}, json={
        "service_name": service_x.service_name, "roles": ["reader"],
    })
    assert resp.status_code == 200


async def test_dept_admin_a_cannot_assign_role_in_dept_b(client, dept_admin_a_token, user_b, dept_b, service_x, db):
    """dept_admin_a should not be able to assign roles for user_b in dept_b."""
    from tests.conftest import _grant_service
    await _grant_service(db, dept_b.id, service_x.service_name)
    url = URL_TPL.format(user_id=user_b.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {dept_admin_a_token}"}, json={
        "service_name": service_x.service_name, "roles": ["reader"],
    })
    assert resp.status_code == 403


async def test_service_not_allowed_for_dept_returns_403(client, admin_token, user_b, db):
    from tests.conftest import _make_service
    other_svc = await _make_service(db, "svc_not_in_b")
    url = URL_TPL.format(user_id=user_b.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "service_name": other_svc.service_name, "roles": ["reader"],
    })
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"


async def test_regular_user_cannot_assign_roles(client, user_a_token, user_b, service_x):
    url = URL_TPL.format(user_id=user_b.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {user_a_token}"}, json={
        "service_name": service_x.service_name, "roles": ["reader"],
    })
    assert resp.status_code == 403


async def test_assign_roles_user_not_found(client, admin_token, service_x):
    url = URL_TPL.format(user_id="usr_nonexistent")
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"}, json={
        "service_name": service_x.service_name, "roles": ["reader"],
    })
    assert resp.status_code == 404
