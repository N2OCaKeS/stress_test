"""Тесты: /api/auth/v1/bots/{bot_id}/roles — выдача service-ролей ботам."""

from datetime import timedelta

from src.utils.time import utcnow

BOTS_URL = "/api/auth/v1/bots"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def _create_bot(client, token, dept_id, name="rb_bot", services=None):
    resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": services or []},
    )
    return resp.json()


async def _issue_bot_token(client, token, bot_id, name="rb_tok"):
    resp = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )
    return resp.json()["token"]


# ── Assign ────────────────────────────────────────────────────────────────────

async def test_admin_assigns_role_to_bot(client, admin_token, dept_a_with_service, service_x):
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id, services=[service_x.service_name]
    )
    resp = await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )
    assert resp.status_code == 201
    assert resp.json()["roles"] == ["reader"]


async def test_dept_admin_assigns_role_in_own_dept(
    client, admin_token, dept_admin_a_token, dept_a_with_service, service_x,
):
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="dept_admin_bot", services=[service_x.service_name],
    )
    resp = await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"service_name": service_x.service_name, "roles": ["operator"]},
    )
    assert resp.status_code == 201


async def test_dept_admin_cannot_assign_role_in_other_dept(
    client, admin_token, dept_admin_a_token, dept_b, service_x, db,
):
    from tests.conftest import _grant_service
    await _grant_service(db, dept_b.id, service_x.service_name)
    bot = await _create_bot(
        client, admin_token, dept_b.id, name="dept_b_bot",
        services=[service_x.service_name],
    )
    resp = await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BOT_ROLE_MGMT_FORBIDDEN"


async def test_assign_role_for_service_not_in_bot_allowed(
    client, admin_token, dept_a_with_service, service_x,
):
    """Bot has empty allowed_services — assigning a role must fail."""
    bot = await _create_bot(client, admin_token, dept_a_with_service.id, services=[])
    resp = await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "SERVICE_NOT_IN_BOT_ALLOWED"


async def test_assign_undefined_role_returns_422(
    client, admin_token, dept_a_with_service, service_x,
):
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id, services=[service_x.service_name]
    )
    resp = await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["does_not_exist"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_SERVICE_ROLE"


async def test_assign_to_nonexistent_bot_returns_404(client, admin_token, service_x):
    resp = await client.post(
        f"{BOTS_URL}/bot_ghost/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )
    assert resp.status_code == 404


# ── List ──────────────────────────────────────────────────────────────────────

async def test_admin_lists_bot_roles(client, admin_token, dept_a_with_service, service_x):
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id, services=[service_x.service_name]
    )
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader", "operator"]},
    )
    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["service_name"] == service_x.service_name
    assert sorted(body[0]["roles"]) == ["operator", "reader"]


async def test_regular_user_cannot_list_bot_roles(
    client, admin_token, user_a_token, dept_a_with_service, service_x,
):
    bot = await _create_bot(client, admin_token, dept_a_with_service.id)
    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert resp.status_code == 403


# ── Revoke ────────────────────────────────────────────────────────────────────

async def test_admin_revokes_bot_roles(client, admin_token, dept_a_with_service, service_x):
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id, services=[service_x.service_name]
    )
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )
    resp = await client.delete(
        f"{BOTS_URL}/{bot['bot_id']}/roles/{service_x.service_name}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    list_resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_resp.json() == []


# ── Introspect propagation ────────────────────────────────────────────────────

async def test_introspect_returns_bot_service_roles(
    client, admin_token, dept_a_with_service, service_x,
):
    """A bot token introspect must contain the service_roles assigned to the bot."""
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id, services=[service_x.service_name]
    )
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader", "operator"]},
    )
    raw = await _issue_bot_token(client, admin_token, bot["bot_id"])

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    body = resp.json()
    assert body["active"] is True
    assert body["subject_type"] == "bot"
    assert service_x.service_name in body["allowed_services"]
    assert sorted(body["service_roles"][service_x.service_name]) == ["operator", "reader"]


async def test_introspect_filters_roles_by_effective_services(
    client, admin_token, dept_a_with_service, service_x, db,
):
    """If a service is removed from bot.allowed_services, its roles must not
    leak through introspect."""
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id, services=[service_x.service_name]
    )
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )
    # Drop the service from bot.allowed_services via update endpoint
    await client.patch(
        f"{BOTS_URL}/{bot['bot_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"allowed_services": []},
    )
    raw = await _issue_bot_token(client, admin_token, bot["bot_id"], name="post_drop")
    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    body = resp.json()
    assert body["active"] is True
    assert body["service_roles"] == {}


# ── Cascade deactivation ──────────────────────────────────────────────────────

async def test_role_def_delete_cascades_to_bot(
    client, admin_token, dept_a_with_service, service_x,
):
    """Deleting a role definition revokes the role from any bot in that
    (department, service)."""
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id, services=[service_x.service_name]
    )
    role_url = f"/api/auth/v1/departments/{dept_a_with_service.id}/services/{service_x.service_name}/roles"
    # Make sure we use a non-system role we can delete
    await client.post(role_url, headers={"Authorization": f"Bearer {admin_token}"},
                      json={"role_name": "ephemeral"})
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["ephemeral"]},
    )

    await client.delete(
        f"{role_url}/ephemeral",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    list_resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_resp.json() == []
