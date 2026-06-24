"""Интеграционные тесты CRUD макросов консоли (/api/server/v1/console-macros).

Покрытие:
* GET — пользователь видит свои личные + системные своего отдела, не видит
  чужие личные и системные чужого отдела; сортировка (is_system, display_order).
* POST — личный заводит любой; системный — только department_admin своего
  отдела (обычный пользователь → 403).
* PATCH/DELETE — личный правит/удаляет только владелец (чужой → 404);
  системный — только department_admin отдела.
"""

import pytest

BASE = "/api/server/v1/console-macros"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_create_personal_macro_any_user(client, reader_token_a):
    """Личный макрос заводит любой аутентифицированный пользователь."""
    resp = await client.post(
        BASE,
        json={"name": "ll", "command_text": "ls -la", "display_order": 1},
        headers=_auth(reader_token_a),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_system"] is False
    assert body["user_id"] is not None
    assert body["command_text"] == "ls -la"


@pytest.mark.asyncio
async def test_create_system_macro_requires_department_admin(
    client, reader_token_a
):
    """Обычный пользователь не может создать системный макрос → 403."""
    resp = await client.post(
        BASE,
        json={"name": "sys", "command_text": "uptime", "is_system": True},
        headers=_auth(reader_token_a),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "CONSOLE_MACRO_SYSTEM_REQUIRES_DEPARTMENT_ADMIN"


@pytest.mark.asyncio
async def test_create_system_macro_by_department_admin(client, admin_token):
    """department_admin своего отдела создаёт системный макрос."""
    resp = await client.post(
        BASE,
        json={"name": "sys", "command_text": "uptime", "is_system": True},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_system"] is True
    assert body["user_id"] is None
    assert body["department_id"] == "dep_a"


@pytest.mark.asyncio
async def test_list_shows_own_personal_and_dept_system(
    client, reader_token_a, operator_token_a, admin_token, reader_token_b
):
    """List отдаёт мои личные + системные отдела; чужие личные/чужой отдел скрыты."""
    # Мой личный (reader_a)
    await client.post(
        BASE, json={"name": "mine", "command_text": "whoami", "display_order": 5},
        headers=_auth(reader_token_a),
    )
    # Чужой личный в том же отделе (operator_a) — не должен попасть в выдачу reader_a
    await client.post(
        BASE, json={"name": "other", "command_text": "id"},
        headers=_auth(operator_token_a),
    )
    # Системный отдела dep_a (department_admin)
    await client.post(
        BASE, json={"name": "sys-a", "command_text": "uptime", "is_system": True, "display_order": 2},
        headers=_auth(admin_token),
    )
    # Личный пользователя из другого отдела (dep_b)
    await client.post(
        BASE, json={"name": "b-personal", "command_text": "df"},
        headers=_auth(reader_token_b),
    )

    resp = await client.get(BASE, headers=_auth(reader_token_a))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    names = {i["name"] for i in items}
    assert "mine" in names          # свой личный
    assert "sys-a" in names         # системный своего отдела
    assert "other" not in names     # чужой личный того же отдела
    assert "b-personal" not in names  # личный другого отдела

    # Сортировка: личные (is_system=false) идут раньше системных.
    flags = [i["is_system"] for i in items]
    assert flags == sorted(flags)


@pytest.mark.asyncio
async def test_list_other_dept_does_not_see_system(client, admin_token, reader_token_b):
    """Системный макрос dep_a не виден пользователю dep_b."""
    await client.post(
        BASE, json={"name": "sys-a", "command_text": "uptime", "is_system": True},
        headers=_auth(admin_token),
    )
    resp = await client.get(BASE, headers=_auth(reader_token_b))
    assert resp.status_code == 200
    assert all(i["name"] != "sys-a" for i in resp.json())


@pytest.mark.asyncio
async def test_owner_updates_own_personal(client, reader_token_a):
    """Владелец правит свой личный макрос."""
    created = (await client.post(
        BASE, json={"name": "x", "command_text": "ls"},
        headers=_auth(reader_token_a),
    )).json()
    resp = await client.patch(
        f"{BASE}/{created['id']}",
        json={"command_text": "ls -la", "display_order": 9},
        headers=_auth(reader_token_a),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["command_text"] == "ls -la"
    assert body["display_order"] == 9


@pytest.mark.asyncio
async def test_other_user_cannot_patch_personal(
    client, reader_token_a, operator_token_a
):
    """Чужой личный макрос → 404 (существование не светится)."""
    created = (await client.post(
        BASE, json={"name": "x", "command_text": "ls"},
        headers=_auth(reader_token_a),
    )).json()
    resp = await client.patch(
        f"{BASE}/{created['id']}",
        json={"command_text": "rm -rf"},
        headers=_auth(operator_token_a),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error_code"] == "CONSOLE_MACRO_NOT_FOUND"


@pytest.mark.asyncio
async def test_other_dept_cannot_see_personal_for_delete(
    client, reader_token_a, reader_token_b
):
    """Личный макрос чужого отдела → 404 на DELETE."""
    created = (await client.post(
        BASE, json={"name": "x", "command_text": "ls"},
        headers=_auth(reader_token_a),
    )).json()
    resp = await client.delete(
        f"{BASE}/{created['id']}", headers=_auth(reader_token_b),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_deletes_own_personal(client, reader_token_a):
    """Владелец удаляет свой личный макрос."""
    created = (await client.post(
        BASE, json={"name": "x", "command_text": "ls"},
        headers=_auth(reader_token_a),
    )).json()
    resp = await client.delete(
        f"{BASE}/{created['id']}", headers=_auth(reader_token_a),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    # Больше не виден в списке.
    listing = (await client.get(BASE, headers=_auth(reader_token_a))).json()
    assert all(i["id"] != created["id"] for i in listing)


@pytest.mark.asyncio
async def test_system_macro_patched_only_by_department_admin(
    client, admin_token, reader_token_a
):
    """Системный макрос правит только department_admin; обычный участник отдела видит, но не правит."""
    created = (await client.post(
        BASE, json={"name": "sys", "command_text": "uptime", "is_system": True},
        headers=_auth(admin_token),
    )).json()

    # Обычный пользователь того же отдела видит макрос в списке (visibility),
    # но PATCH запрещён → 403 (не 404 — макрос ему виден).
    listing = (await client.get(BASE, headers=_auth(reader_token_a))).json()
    assert any(i["id"] == created["id"] for i in listing)

    resp = await client.patch(
        f"{BASE}/{created['id']}",
        json={"command_text": "halt"},
        headers=_auth(reader_token_a),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "CONSOLE_MACRO_FORBIDDEN"

    # department_admin правит успешно.
    ok = await client.patch(
        f"{BASE}/{created['id']}",
        json={"command_text": "halt"},
        headers=_auth(admin_token),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["command_text"] == "halt"


@pytest.mark.asyncio
async def test_system_macro_cross_dept_admin_gets_404(
    client, admin_token, admin_token_b
):
    """department_admin чужого отдела не видит системный макрос dep_a → 404."""
    created = (await client.post(
        BASE, json={"name": "sys", "command_text": "uptime", "is_system": True},
        headers=_auth(admin_token),
    )).json()
    resp = await client.delete(
        f"{BASE}/{created['id']}", headers=_auth(admin_token_b),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_system_macro_deleted_by_department_admin(client, admin_token):
    """department_admin удаляет системный макрос своего отдела."""
    created = (await client.post(
        BASE, json={"name": "sys", "command_text": "uptime", "is_system": True},
        headers=_auth(admin_token),
    )).json()
    resp = await client.delete(
        f"{BASE}/{created['id']}", headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_get_requires_token(client):
    """Без bearer'а — 401."""
    resp = await client.get(BASE)
    assert resp.status_code == 401
