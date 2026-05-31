"""Regression: DELETE /groups/{id} must drop all privileges that came via that group.

До фикса `delete_group` помечал группу `is_active=False`, но
`_group_ids_for_user`/`_group_ids_for_bot` не фильтровали по
`UserGroup.is_active`, так что юзер сохранял `allowed_services` и
`service_roles` через soft-deleted группу до фактического удаления membership.
"""

ME_URL = "/api/auth/v1/me"
GROUPS_URL = "/api/auth/v1/groups"
USERS_URL = "/api/auth/v1/users"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def _create_group(client, token, dept_id, name):
    resp = await client.post(
        GROUPS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"department_id": dept_id, "name": name, "display_name": name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


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


async def _add_member(client, token, group_id, user_id):
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
        json={"user_id": user_id},
    )
    assert resp.status_code == 201, resp.text


async def _delete_group(client, token, group_id):
    resp = await client.delete(
        f"{GROUPS_URL}/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def test_delete_group_strips_group_privileges_from_me(
    client, admin_token, user_a, user_a_token, dept_a_with_service, service_x,
):
    """Создаём группу с access+role, добавляем юзера, удаляем группу.

    После delete'а группа не должна расширять `allowed_services` юзера —
    в `/me` не остаётся следа service-роли из удалённой группы.
    """
    group_id = await _create_group(
        client, admin_token, dept_a_with_service.id, "grp_to_delete",
    )
    await _grant_service(client, admin_token, group_id, service_x.service_name)
    await _assign_roles(
        client, admin_token, group_id, service_x.service_name, ["operator"],
    )
    await _add_member(client, admin_token, group_id, user_a.id)

    # Sanity: до удаления группа видна в /me с ролью.
    before = await client.get(
        ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert before.status_code == 200, before.text
    body_before = before.json()
    assert "grp_to_delete" in body_before["groups"]
    assert (
        f"{service_x.service_name}.operator"
        in body_before["groups"]["grp_to_delete"]
    )

    await _delete_group(client, admin_token, group_id)

    after = await client.get(
        ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert after.status_code == 200, after.text
    body_after = after.json()
    # Группа выпала из identity полностью.
    assert "grp_to_delete" not in body_after["groups"]
    # И не осталось дополнительной операторской роли — у user_a остаётся
    # только direct-assignment'ный reader из фикстуры.
    sx_roles = body_after.get("service_roles", {}).get(service_x.service_name, [])
    assert "operator" not in sx_roles


async def test_delete_group_strips_group_privileges_from_introspect(
    client, admin_token, user_a, user_a_token, dept_a_with_service, service_x,
    service_auth_headers,
):
    """Симметрия для introspect (service-to-service путь): roles через удалённую
    группу пропадают так же, как и в `/me`."""
    group_id = await _create_group(
        client, admin_token, dept_a_with_service.id, "grp_introspect_drop",
    )
    await _grant_service(client, admin_token, group_id, service_x.service_name)
    await _assign_roles(
        client, admin_token, group_id, service_x.service_name, ["operator"],
    )
    await _add_member(client, admin_token, group_id, user_a.id)

    await _delete_group(client, admin_token, group_id)

    resp = await client.post(
        INTROSPECT_URL,
        headers={**service_auth_headers},
        json={"token": user_a_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    sx_roles = body.get("service_roles", {}).get(service_x.service_name, [])
    assert "operator" not in sx_roles


async def test_delete_group_strips_group_privileges_for_bot(
    client, admin_token, dept_a_with_service, service_x, db,
):
    """Симметрично юзерам: бот, лишённый группы (через её soft-delete), не должен
    наследовать `service_roles` от группы. Проверяем через repo-уровень
    `get_roles_for_bot` — endpoint'ы CRUD-ботов покрыты другими тестами."""
    from src.repositories.bots import BotRepository
    from src.repositories.groups import GroupRepository
    from src.utils.ids import _new_id

    # Создаём бота напрямую в БД (фикстуры bot'а в conftest нет; копи-паста
    # `_make_user`).
    from src.models import BotAccount

    bot = BotAccount(
        id=_new_id("bot_"),
        name="t_bot_grp_priv",
        department_id=dept_a_with_service.id,
        is_active=True,
        created_by=None,
    )
    db.add(bot)
    await db.flush()

    group_id = await _create_group(
        client, admin_token, dept_a_with_service.id, "grp_bot_priv",
    )
    await _grant_service(client, admin_token, group_id, service_x.service_name)
    await _assign_roles(
        client, admin_token, group_id, service_x.service_name, ["operator"],
    )

    # Добавляем бота в группу через endpoint.
    resp = await client.post(
        f"{GROUPS_URL}/{group_id}/bots",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"bot_id": bot.id},
    )
    assert resp.status_code == 201, resp.text

    group_repo = GroupRepository(db)
    before = await group_repo.get_roles_for_bot(bot.id)
    assert before.get(service_x.service_name) == ["operator"]

    await _delete_group(client, admin_token, group_id)

    after = await group_repo.get_roles_for_bot(bot.id)
    assert service_x.service_name not in after, (
        f"bot retained role through soft-deleted group: {after!r}"
    )
