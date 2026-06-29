"""Тесты: /api/auth/v1/services — управление платформенными сервисами."""

URL = "/api/auth/v1/services"


async def test_admin_creates_service(client, admin_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": "new_svc"})
    assert resp.status_code == 201
    assert resp.json()["service_name"] == "new_svc"


async def test_duplicate_service_returns_409(client, admin_token, service_x):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"},
                              json={"service_name": service_x.service_name})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "SERVICE_ALREADY_EXISTS"


async def test_dept_admin_cannot_create_service(client, dept_admin_a_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"},
                              json={"service_name": "hack_svc"})
    assert resp.status_code == 403


async def test_regular_user_cannot_create_service(client, user_a_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"service_name": "hack_svc2"})
    assert resp.status_code == 403


async def test_list_services_requires_auth(client, admin_token, service_x):
    resp = await client.get(URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [s["service_name"] for s in resp.json()]
    assert service_x.service_name in names


async def test_dept_admin_lists_only_own_department_services(
    client, dept_admin_a_token, dept_a_with_service, service_x, db,
):
    # department_admin видит только сервисы своего отдела (service_x выдан dept_a),
    # а не весь регистр: чужой сервис без гранта в список не попадает.
    from tests.conftest import _make_service

    other = await _make_service(db, "other_svc_no_grant")
    resp = await client.get(URL, headers={"Authorization": f"Bearer {dept_admin_a_token}"})
    assert resp.status_code == 200
    names = [s["service_name"] for s in resp.json()]
    assert service_x.service_name in names
    assert other.service_name not in names


async def test_account_admin_lists_all_services(client, admin_token, service_x, db):
    # account_admin видит весь регистр, включая сервисы без гранта отделам.
    from tests.conftest import _make_service

    other = await _make_service(db, "other_svc_for_admin")
    resp = await client.get(URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [s["service_name"] for s in resp.json()]
    assert service_x.service_name in names
    assert other.service_name in names


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


# ── Cascade after delete ──────────────────────────────────────────────────────

ME_URL = "/api/auth/v1/me"


async def test_delete_service_revokes_department_access(client, admin_token, user_a, user_a_token, service_x):
    """После удаления сервиса у отдела не остаётся к нему доступа — /me не показывает service."""
    # Sanity: до удаления user_a (из dept_a с грантом на service_x) видит service_x
    before = (await client.get(ME_URL, headers={"Authorization": f"Bearer {user_a_token}"})).json()
    assert service_x.service_name in before["allowed_services"]

    await client.delete(f"{URL}/{service_x.service_name}",
                        headers={"Authorization": f"Bearer {admin_token}"})

    after = (await client.get(ME_URL, headers={"Authorization": f"Bearer {user_a_token}"})).json()
    assert service_x.service_name not in after["allowed_services"]


async def test_delete_service_deactivates_user_service_roles(client, admin_token, user_a, user_a_token, service_x):
    """После удаления сервиса роли пользователя по нему деактивируются — /me не показывает roles."""
    before = (await client.get(ME_URL, headers={"Authorization": f"Bearer {user_a_token}"})).json()
    assert service_x.service_name in before["service_roles"]

    await client.delete(f"{URL}/{service_x.service_name}",
                        headers={"Authorization": f"Bearer {admin_token}"})

    after = (await client.get(ME_URL, headers={"Authorization": f"Bearer {user_a_token}"})).json()
    assert service_x.service_name not in after["service_roles"]
