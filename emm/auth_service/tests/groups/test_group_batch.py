"""group_service: batch-выборки вместо N+1.

`list_members`, `list_user_groups` и `get_user_permissions` раньше делали
per-item запросы (`user_repo.get_by_id` на каждого члена, `group_repo.get`
на каждую membership, `list_service_access`/`list_roles` на каждую группу).
Теперь — IN-выборки по списку id. Проверяем характер запросов (spy на
per-item repo-методы) при нескольких members/groups.
"""

import pytest

from src.repositories.groups import GroupRepository
from src.repositories.users import UserRepository

GROUPS_URL = "/api/auth/v1/groups"
USERS_URL = "/api/auth/v1/users"


async def _create_group(client, token, department_id, name):
    return await client.post(
        GROUPS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"department_id": department_id, "name": name},
    )


async def _add_member(client, token, group_id, user_id):
    return await client.post(
        f"{GROUPS_URL}/{group_id}/members",
        headers={"Authorization": f"Bearer {token}"},
        json={"user_id": user_id},
    )


async def _make_user_in_dept(db, dept_id, username):
    from src.core.security import hash_password
    from src.models import User
    from src.utils.ids import _new_id

    u = User(
        id=_new_id("usr_"), username=username,
        password_hash=hash_password("User12345678!"),
        department_id=dept_id, is_active=True, status="active",
    )
    db.add(u)
    await db.flush()
    return u


@pytest.fixture()
def spy_per_item(monkeypatch):
    counts = {
        "user_get_by_id": 0,
        "user_list_by_ids": 0,
        "group_get": 0,
        "group_list_active_by_ids": 0,
        "list_service_access": 0,
        "list_roles": 0,
        "list_service_access_for_groups": 0,
        "list_roles_for_groups": 0,
    }

    pairs = [
        (UserRepository, "get_by_id", "user_get_by_id"),
        (UserRepository, "list_by_ids", "user_list_by_ids"),
        (GroupRepository, "get", "group_get"),
        (GroupRepository, "list_active_by_ids", "group_list_active_by_ids"),
        (GroupRepository, "list_service_access", "list_service_access"),
        (GroupRepository, "list_roles", "list_roles"),
        (GroupRepository, "list_service_access_for_groups", "list_service_access_for_groups"),
        (GroupRepository, "list_roles_for_groups", "list_roles_for_groups"),
    ]
    for cls, method, key in pairs:
        orig = getattr(cls, method)

        def make(orig, key):
            async def wrapper(self, *a, **k):
                counts[key] += 1
                return await orig(self, *a, **k)
            return wrapper

        monkeypatch.setattr(cls, method, make(orig, key))
    return counts


async def test_list_members_batches_user_lookup(
    spy_per_item, client, admin_token, dept_a, db,
):
    """3 члена группы → один `list_by_ids`, ноль per-member `get_by_id`."""
    members = [await _make_user_in_dept(db, dept_a.id, f"mem_{i}") for i in range(3)]
    await db.commit()

    gid = (await _create_group(client, admin_token, dept_a.id, "members_batch")).json()["id"]
    for m in members:
        await _add_member(client, admin_token, gid, m.id)

    spy_per_item["user_get_by_id"] = 0  # обнуляем шум setup'а (add_member и т.д.)
    spy_per_item["user_list_by_ids"] = 0

    resp = await client.get(
        f"{GROUPS_URL}/{gid}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 3
    assert spy_per_item["user_list_by_ids"] == 1, "ожидали один batch-lookup юзеров"
    # Единственный get_by_id — резолв актора запроса в auth-dep
    # (dependencies/auth.py), не per-member: членов тянет один list_by_ids
    # (assert выше), сам list_members get_by_id не зовёт. Per-member паттерн
    # дал бы >= числа членов.
    assert spy_per_item["user_get_by_id"] <= 1, "per-member get_by_id устранён (остаётся только резолв актора)"


async def test_list_user_groups_batches_group_lookup(
    spy_per_item, client, admin_token, dept_a, user_a, user_a_token, db,
):
    """Юзер в 3 группах → один `list_active_by_ids` per stage, ноль per-membership `get`.

    Запрос делает два разных batch-lookup: первый — внутри `collect_user_permissions`
    (auth-dependency считает `groups` для identity), второй — внутри
    `list_user_groups` endpoint'а. Оба batched, per-membership get'ов нет.
    """
    gids = []
    for i in range(3):
        gid = (await _create_group(client, admin_token, dept_a.id, f"ug_{i}")).json()["id"]
        await _add_member(client, admin_token, gid, user_a.id)
        gids.append(gid)

    spy_per_item["group_get"] = 0
    spy_per_item["group_list_active_by_ids"] = 0

    resp = await client.get(
        f"{USERS_URL}/{user_a.id}/groups",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 3
    # Два batch-lookup'а (identity-кэш + endpoint-резолв), не пер-группа.
    assert spy_per_item["group_list_active_by_ids"] == 2
    assert spy_per_item["group_get"] == 0, "per-membership group_get устранён"


async def test_get_user_permissions_batches_group_access_and_roles(
    spy_per_item, client, admin_token, dept_a_with_service, service_x, user_a, db,
):
    """Несколько групп с access+roles → batch-выборки, ноль per-group запросов."""
    gids = []
    for i in range(3):
        gid = (await _create_group(client, admin_token, dept_a_with_service.id, f"perm_{i}")).json()["id"]
        await _add_member(client, admin_token, gid, user_a.id)
        await client.post(
            f"{GROUPS_URL}/{gid}/services",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": service_x.service_name},
        )
        await client.post(
            f"{GROUPS_URL}/{gid}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": service_x.service_name, "roles": ["reader"]},
        )
        gids.append(gid)

    # Обнуляем шум setup'а (assign_group_roles читает list_members и т.д.).
    for k in spy_per_item:
        spy_per_item[k] = 0

    resp = await client.get(
        f"{USERS_URL}/{user_a.id}/permissions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["groups"]) == 3

    assert spy_per_item["group_list_active_by_ids"] == 1
    assert spy_per_item["list_service_access_for_groups"] == 1
    assert spy_per_item["list_roles_for_groups"] == 1
    # Ни одного per-group запроса.
    assert spy_per_item["group_get"] == 0
    assert spy_per_item["list_service_access"] == 0
    assert spy_per_item["list_roles"] == 0
