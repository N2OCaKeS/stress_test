"""Тесты: /api/auth/v1/services — управление платформенными сервисами."""

URL = "/api/auth/v1/services"


async def test_admin_creates_service(client, admin_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": "new_svc", "display_name": "New Service"})
    assert resp.status_code == 201
    assert resp.json()["service_name"] == "new_svc"


async def test_duplicate_service_returns_409(client, admin_token, service_x):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name, "display_name": "Dup"})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "SERVICE_ALREADY_EXISTS"


async def test_dept_admin_cannot_create_service(client, dept_admin_a_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"service_name": "hack_svc", "display_name": "Hack"})
    assert resp.status_code == 403


async def test_regular_user_cannot_create_service(client, user_a_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"service_name": "hack_svc2", "display_name": "Hack"})
    assert resp.status_code == 403


async def test_list_services_requires_auth(client, admin_token, service_x):
    resp = await client.get(URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [s["service_name"] for s in resp.json()]
    assert service_x.service_name in names


async def test_admin_deletes_service(client, admin_token, db):
    from tests.conftest import _make_service
    svc = await _make_service(db, "to_delete_svc")
    resp = await client.delete(f"{URL}/{svc.service_name}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200


async def test_delete_nonexistent_service_returns_404(client, admin_token):
    resp = await client.delete(f"{URL}/does_not_exist", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 404


async def test_dept_admin_cannot_delete_service(client, dept_admin_a_token, service_x):
    resp = await client.delete(f"{URL}/{service_x.service_name}",
                                headers={"Authorization": f"Bearer {dept_admin_a_token}"})
    assert resp.status_code == 403
