"""Тесты: членство ботов в группах — /api/auth/v1/groups/{gid}/bots.

Бот наследует service-роли группы (как юзер), пересечённые с `bot.allowed_services`.
Группа даёт роли, но не расширяет список сервисов бота.
"""

from datetime import timedelta

from src.utils.time import utcnow

GROUPS_URL = "/api/auth/v1/groups"
BOTS_URL = "/api/auth/v1/bots"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def _create_group(client, token, department_id, name="bg_group"):
    return (await client.post(
        GROUPS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"department_id": department_id, "name": name},
    )).json()


async def _create_bot(client, token, dept_id, name="bg_bot", services=None):
    return (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": services or []},
    )).json()


async def _issue_bot_token(client, token, bot_id, name="bg_tok"):
    resp = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
    )
    return resp.json()["token"]


async def _grant_group_service(client, token, group_id, service_name):
    return await client.post(
        f"{GROUPS_URL}/{group_id}/services",
        headers={"Authorization": f"Bearer {token}"},
        json={"service_name": service_name},
    )


async def _assign_group_roles(client, token, group_id, service_name, roles):
    return await client.post(
        f"{GROUPS_URL}/{group_id}/roles",
        headers={"Authorization": f"Bearer {token}"},
        json={"service_name": service_name, "roles": roles},
    )


async def _add_bot_to_group(client, token, group_id, bot_id):
    return await client.post(
        f"{GROUPS_URL}/{group_id}/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"bot_id": bot_id},
    )


# ── Membership management ─────────────────────────────────────────────────────

async def test_admin_adds_bot_to_group(client, admin_token, dept_a_with_service, service_x):
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="add_bot_grp")
    bot = await _create_bot(client, admin_token, dept_a_with_service.id, services=[service_x.service_name])
    resp = await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["bot_id"] == bot["bot_id"]
    assert body["name"] == bot["name"]


async def test_dept_admin_adds_bot_in_own_dept(
    client, admin_token, dept_admin_a_token, dept_a_with_service, service_x,
):
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="da_bot_grp")
    bot = await _create_bot(client, admin_token, dept_a_with_service.id, name="da_bot")
    resp = await _add_bot_to_group(client, dept_admin_a_token, grp["id"], bot["bot_id"])
    assert resp.status_code == 201


async def test_cross_dept_bot_rejected(client, admin_token, dept_a, dept_b):
    """Группа в dept_a, бот в dept_b — отказ GROUP_DEPARTMENT_MISMATCH (403,
    как у user-member: это AuthorizationError, см. test_groups)."""
    grp = await _create_group(client, admin_token, dept_a.id, name="cross_bot_grp")
    bot = await _create_bot(client, admin_token, dept_b.id, name="cross_bot")
    resp = await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "GROUP_DEPARTMENT_MISMATCH"


async def test_dept_admin_other_dept_forbidden(
    client, admin_token, dept_admin_a_token, dept_b,
):
    grp = await _create_group(client, admin_token, dept_b.id, name="da_other_bot_grp")
    bot = await _create_bot(client, admin_token, dept_b.id, name="da_other_bot")
    resp = await _add_bot_to_group(client, dept_admin_a_token, grp["id"], bot["bot_id"])
    assert resp.status_code == 403


async def test_regular_user_cannot_add_bot(
    client, admin_token, user_a_token, dept_a_with_service,
):
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="reg_bot_grp")
    bot = await _create_bot(client, admin_token, dept_a_with_service.id, name="reg_bot")
    resp = await _add_bot_to_group(client, user_a_token, grp["id"], bot["bot_id"])
    assert resp.status_code == 403


async def test_duplicate_bot_membership_returns_409(
    client, admin_token, dept_a_with_service,
):
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="dup_bot_grp")
    bot = await _create_bot(client, admin_token, dept_a_with_service.id, name="dup_bot")
    first = await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])
    assert first.status_code == 201
    second = await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])
    assert second.status_code == 409
    assert second.json()["error_code"] == "ALREADY_GROUP_MEMBER"


async def test_add_nonexistent_bot_returns_404(client, admin_token, dept_a_with_service):
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="ghost_bot_grp")
    resp = await _add_bot_to_group(client, admin_token, grp["id"], "bot_ghost")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "BOT_NOT_FOUND"


async def test_remove_nonmember_bot_returns_404(client, admin_token, dept_a_with_service):
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="rm_ghost_grp")
    bot = await _create_bot(client, admin_token, dept_a_with_service.id, name="rm_ghost_bot")
    resp = await client.delete(
        f"{GROUPS_URL}/{grp['id']}/bots/{bot['bot_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "MEMBER_NOT_FOUND"


async def test_list_bot_members(client, admin_token, dept_a_with_service):
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="list_bots_grp")
    bot1 = await _create_bot(client, admin_token, dept_a_with_service.id, name="lb_bot1")
    bot2 = await _create_bot(client, admin_token, dept_a_with_service.id, name="lb_bot2")
    await _add_bot_to_group(client, admin_token, grp["id"], bot1["bot_id"])
    await _add_bot_to_group(client, admin_token, grp["id"], bot2["bot_id"])
    resp = await client.get(
        f"{GROUPS_URL}/{grp['id']}/bots",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    ids = {m["bot_id"] for m in resp.json()}
    assert ids == {bot1["bot_id"], bot2["bot_id"]}


# ── Role inheritance via introspect ───────────────────────────────────────────

async def test_bot_inherits_group_roles(client, admin_token, dept_a_with_service, service_x):
    """Бот в группе с ролью операнда наследует роль через introspect, хотя прямых ролей нет."""
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="inh_grp")
    await _grant_group_service(client, admin_token, grp["id"], service_x.service_name)
    await _assign_group_roles(client, admin_token, grp["id"], service_x.service_name, ["operator"])

    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="inh_bot", services=[service_x.service_name],
    )
    await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])

    raw = await _issue_bot_token(client, admin_token, bot["bot_id"])
    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    body = resp.json()
    assert body["active"] is True
    assert body["subject_type"] == "bot"
    assert service_x.service_name in body["allowed_services"]
    assert body["service_roles"][service_x.service_name] == ["operator"]


async def test_group_role_for_service_outside_allowed_does_not_appear(
    client, admin_token, dept_a_with_service, service_x,
):
    """Сервис есть у группы, но НЕ в bot.allowed_services — роль не появляется (∩ allowed_services)."""
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="fence_grp")
    await _grant_group_service(client, admin_token, grp["id"], service_x.service_name)
    await _assign_group_roles(client, admin_token, grp["id"], service_x.service_name, ["operator"])

    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id, name="fence_bot", services=[],
    )
    await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])

    raw = await _issue_bot_token(client, admin_token, bot["bot_id"])
    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    body = resp.json()
    assert body["active"] is True
    assert body["service_roles"] == {}
    assert service_x.service_name not in body["allowed_services"]


async def test_removing_bot_from_group_drops_inherited_roles(
    client, admin_token, dept_a_with_service, service_x,
):
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="drop_grp")
    await _grant_group_service(client, admin_token, grp["id"], service_x.service_name)
    await _assign_group_roles(client, admin_token, grp["id"], service_x.service_name, ["reader"])

    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="drop_bot", services=[service_x.service_name],
    )
    await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])
    raw = await _issue_bot_token(client, admin_token, bot["bot_id"])

    before = (await client.post(INTROSPECT_URL, json={"token": raw})).json()
    assert before["service_roles"][service_x.service_name] == ["reader"]

    remove = await client.delete(
        f"{GROUPS_URL}/{grp['id']}/bots/{bot['bot_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert remove.status_code == 200

    after = (await client.post(INTROSPECT_URL, json={"token": raw})).json()
    assert after["active"] is True
    assert after["service_roles"] == {}


async def test_direct_and_group_roles_merge_without_dupes(
    client, admin_token, dept_a_with_service, service_x,
):
    """Прямые bot_service_roles + групповые роли мёржатся без дублей."""
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="merge_grp")
    await _grant_group_service(client, admin_token, grp["id"], service_x.service_name)
    await _assign_group_roles(client, admin_token, grp["id"], service_x.service_name, ["reader", "operator"])

    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="merge_bot", services=[service_x.service_name],
    )
    # Direct roles overlap (reader) with the group set plus add a distinct one (guest).
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader", "guest"]},
    )
    await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])

    raw = await _issue_bot_token(client, admin_token, bot["bot_id"])
    body = (await client.post(INTROSPECT_URL, json={"token": raw})).json()
    assert sorted(body["service_roles"][service_x.service_name]) == ["guest", "operator", "reader"]


async def test_mixed_group_resolves_user_and_bot_independently(
    client, admin_token, dept_a_with_service, service_x, user_a, db,
):
    """Смешанная группа (юзер + бот): у каждого свои роли резолвятся из группы."""
    grp = await _create_group(client, admin_token, dept_a_with_service.id, name="mixed_grp")
    await _grant_group_service(client, admin_token, grp["id"], service_x.service_name)
    await _assign_group_roles(client, admin_token, grp["id"], service_x.service_name, ["operator"])

    # user_a is in dept_a_with_service; attach to the group.
    user_add = await client.post(
        f"{GROUPS_URL}/{grp['id']}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_a.id},
    )
    assert user_add.status_code == 201

    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="mixed_bot", services=[service_x.service_name],
    )
    await _add_bot_to_group(client, admin_token, grp["id"], bot["bot_id"])

    # Bot resolution: inherits operator from group.
    raw = await _issue_bot_token(client, admin_token, bot["bot_id"])
    bot_body = (await client.post(INTROSPECT_URL, json={"token": raw})).json()
    assert "operator" in bot_body["service_roles"][service_x.service_name]

    # User resolution: also inherits operator (plus their fixture reader role).
    from tests.conftest import _login
    user_token = await _login(client, "t_user_a", "User12345678!")
    user_body = (await client.post(INTROSPECT_URL, json={"token": user_token})).json()
    assert user_body["active"] is True
    assert "operator" in user_body["service_roles"][service_x.service_name]
