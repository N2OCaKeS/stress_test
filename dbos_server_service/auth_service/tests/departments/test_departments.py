"""Тесты: /api/auth/v1/departments — управление отделами и доступом к сервисам."""

CREATE_URL = "/api/auth/v1/departments"
GRANT_URL = "/api/auth/v1/departments/{dept_id}/services"
REVOKE_URL = "/api/auth/v1/departments/{dept_id}/services/{svc_name}"


# ── Create department ─────────────────────────────────────────────────────────

async def test_admin_creates_department(client, admin_token):
    resp = await client.post(CREATE_URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"name": "finance", "display_name": "Finance"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "finance"


async def test_duplicate_dept_name_returns_409(client, admin_token, dept_a):
    resp = await client.post(CREATE_URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"name": dept_a.name, "display_name": "Dup"})
    assert resp.status_code == 409


async def test_dept_admin_cannot_create_department(client, dept_admin_a_token):
    resp = await client.post(CREATE_URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"name": "new_dept", "display_name": "New"})
    assert resp.status_code == 403


async def test_list_departments_returns_active(client, admin_token, dept_a, dept_b):
    resp = await client.get(CREATE_URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()]
    assert dept_a.name in names
    assert dept_b.name in names


# ── Grant / revoke service access ─────────────────────────────────────────────

async def test_admin_grants_service_to_dept(client, admin_token, dept_b, service_x):
    url = GRANT_URL.format(dept_id=dept_b.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 201


async def test_duplicate_grant_returns_409(client, admin_token, dept_a_with_service, service_x):
    url = GRANT_URL.format(dept_id=dept_a_with_service.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 409


async def test_grant_nonexistent_service_returns_404(client, admin_token, dept_a):
    url = GRANT_URL.format(dept_id=dept_a.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": "does_not_exist"})
    assert resp.status_code == 404


async def test_dept_admin_cannot_grant_service(client, dept_admin_a_token, dept_a, service_x):
    url = GRANT_URL.format(dept_id=dept_a.id)
    resp = await client.post(url, headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 403


async def test_admin_revokes_service_from_dept(client, admin_token, dept_a_with_service, service_x):
    url = REVOKE_URL.format(dept_id=dept_a_with_service.id, svc_name=service_x.service_name)
    resp = await client.delete(url, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


async def test_after_revoke_user_login_loses_service(client, admin_token, user_a, user_a_token,
                                                      dept_a_with_service, service_x):
    url = REVOKE_URL.format(dept_id=dept_a_with_service.id, svc_name=service_x.service_name)
    await client.delete(url, headers={"Authorization": f"Bearer {admin_token}"})
    resp = await client.post("/api/auth/v1/login", json={"username": "t_user_a", "password": "User1234!"})
    assert service_x.service_name not in resp.json()["identity"]["allowed_services"]
