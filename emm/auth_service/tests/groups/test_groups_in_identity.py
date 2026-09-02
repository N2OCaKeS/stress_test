"""Тесты: поле `groups` в IdentityContext (`/me`) и в IntrospectResponse.

Контракт:
* `{group_name: ["<service>.<role>", ...]}` — какие группы юзера через какие
  роли расширяют его права;
* группы без service-роли (только access) сюда не попадают;
* в introspect (PAT-ветка) поле режется по `pat.allowed_services`.
"""

import os

ME_URL = "/api/auth/v1/me"
GROUPS_URL = "/api/auth/v1/groups"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def _create_group(client, token, dept_id, name):
    resp = await client.post(
        GROUPS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"department_id": dept_id, "name": name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_member(client, token, group_id, user_id):
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
        json={"user_id": user_id},
    )
    assert resp.status_code == 201, resp.text


async def _grant_service(client, token, group_id, service_name):
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/services",
        headers={"Authorization": f"Bearer {token}"},
        json={"service_name": service_name},
    )
    assert resp.status_code == 201, resp.text


async def _assign_roles(client, token, group_id, service_name, roles):
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/roles",
        headers={"Authorization": f"Bearer {token}"},
        json={"service_name": service_name, "roles": roles},
    )
    assert resp.status_code == 201, resp.text


# ── /me identity ─────────────────────────────────────────────────────────────


async def test_me_user_without_groups_has_empty_groups(client, user_a, user_a_token):
    """Юзер не в группах → `groups: {}`."""
    resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "groups" in body
    assert body["groups"] == {}


async def test_me_user_in_group_with_role(
    client, admin_token, user_a, user_a_token, dept_a_with_service, service_x,
):
    """Юзер в группе с 1 ролью → `groups: {name: ['svc.role']}`."""
    group_id = await _create_group(client, admin_token, dept_a_with_service.id, "grp_role_one")
    await _grant_service(client, admin_token, group_id, service_x.service_name)
    await _assign_roles(client, admin_token, group_id, service_x.service_name, ["operator"])
    await _add_member(client, admin_token, group_id, user_a.id)

    resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200, resp.text
    groups = resp.json()["groups"]
    assert "grp_role_one" in groups
    assert groups["grp_role_one"] == [f"{service_x.service_name}.operator"]


async def test_me_group_without_role_not_shown(
    client, admin_token, user_a, user_a_token, dept_a_with_service, service_x,
):
    """Группа с access, но без role — не показывается в `groups`."""
    group_id = await _create_group(client, admin_token, dept_a_with_service.id, "grp_no_role")
    await _grant_service(client, admin_token, group_id, service_x.service_name)
    # no _assign_roles
    await _add_member(client, admin_token, group_id, user_a.id)

    resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 200
    assert "grp_no_role" not in resp.json()["groups"]


# ── introspect ────────────────────────────────────────────────────────────────


async def test_introspect_jwt_returns_groups(
    client, admin_token, user_a, user_a_token, dept_a_with_service, service_x,
    service_auth_headers,
):
    """JWT-introspect возвращает `groups` с роли через группу."""
    group_id = await _create_group(
        client, admin_token, dept_a_with_service.id, "grp_introspect_jwt",
    )
    await _grant_service(client, admin_token, group_id, service_x.service_name)
    await _assign_roles(client, admin_token, group_id, service_x.service_name, ["operator"])
    await _add_member(client, admin_token, group_id, user_a.id)

    resp = await client.post(
        INTROSPECT_URL,
        headers=service_auth_headers,
        json={"token": user_a_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active"] is True
    assert "groups" in body
    assert body["groups"].get("grp_introspect_jwt") == [
        f"{service_x.service_name}.operator",
    ]


async def test_introspect_account_admin_empty_groups(
    client, admin_token, service_auth_headers,
):
    """account_admin → groups пуст (как allowed_services/service_roles)."""
    resp = await client.post(
        INTROSPECT_URL,
        headers=service_auth_headers,
        json={"token": admin_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active"] is True
    assert body["groups"] == {}
