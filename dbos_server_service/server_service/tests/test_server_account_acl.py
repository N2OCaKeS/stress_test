"""E2E: per-account ACL — прямые гранты на учётку поверх ролевой матрицы.

Проверяем: управление грантами гейтится `manage_account_acl`; грант даёт
пользователю без роли доступ (view_password / update / delete / console-резолв);
снятие убирает доступ; dept-изоляция (cross-dept за 404); department_admin
bypass; консоль проходит по per-account `console`-гранту без view_password.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

_API = "/api/server/v1"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def server_a(make_server):
    return await make_server(department_id="dep_a")


@pytest_asyncio.fixture
async def account_a(make_account, server_a):
    return await make_account(server_id=server_a.id, login="svc1")


# ── Управление ACL гейтится manage_account_acl ──────────────────────────────


async def test_admin_can_grant_and_list(client, admin_token, account_a):
    r = await client.post(
        f"{_API}/server-accounts/{account_a.id}/acl",
        headers=_auth(admin_token),
        json={"user_id": "usr_target", "actions": ["view", "view_password"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user_id"] == "usr_target"
    assert set(body["actions"]) == {"view", "view_password"}
    assert body["department_id"] == "dep_a"

    lst = await client.get(
        f"{_API}/server-accounts/{account_a.id}/acl", headers=_auth(admin_token),
    )
    assert lst.status_code == 200
    assert len(lst.json()) == 1


async def test_operator_cannot_manage_acl(client, operator_token_a, account_a):
    r = await client.post(
        f"{_API}/server-accounts/{account_a.id}/acl",
        headers=_auth(operator_token_a),
        json={"user_id": "usr_t", "actions": ["view"]},
    )
    assert r.status_code == 403


async def test_reader_cannot_list_acl(client, reader_token_a, account_a):
    r = await client.get(
        f"{_API}/server-accounts/{account_a.id}/acl", headers=_auth(reader_token_a),
    )
    assert r.status_code == 403


async def test_grant_rejects_unknown_action(client, admin_token, account_a):
    r = await client.post(
        f"{_API}/server-accounts/{account_a.id}/acl",
        headers=_auth(admin_token),
        json={"user_id": "usr_t", "actions": ["view", "manage_account_acl"]},
    )
    assert r.status_code == 422
    assert r.json()["error_code"] == "INVALID_ACL_ACTION"


async def test_acl_cross_dept_hidden_404(client, make_token, account_a):
    """admin чужого отдела не видит/не управляет учёткой dep_a."""
    admin_b = make_token(
        platform_role="department_admin",
        department_id="dep_b",
        service_roles={"server_service": ["admin"]},
    )
    r = await client.get(
        f"{_API}/server-accounts/{account_a.id}/acl", headers=_auth(admin_b),
    )
    assert r.status_code == 404


# ── Грант даёт доступ пользователю без роли ─────────────────────────────────


async def test_grant_gives_view_password_to_roleless_user(
    client, admin_token, make_token, account_a,
):
    target = make_token(
        department_id="dep_a", service_roles={}, user_id="usr_grantee",
    )
    # Без гранта — 403 на карточку.
    pre = await client.get(
        f"{_API}/server-accounts/{account_a.id}", headers=_auth(target),
    )
    assert pre.status_code == 403

    g = await client.post(
        f"{_API}/server-accounts/{account_a.id}/acl",
        headers=_auth(admin_token),
        json={"user_id": "usr_grantee", "actions": ["view", "view_password"]},
    )
    assert g.status_code == 201

    # С грантом — 200 + пароль в password_b64.
    post = await client.get(
        f"{_API}/server-accounts/{account_a.id}", headers=_auth(target),
    )
    assert post.status_code == 200, post.text
    assert post.json()["password_b64"] is not None


async def test_revoke_removes_access(client, admin_token, make_token, account_a):
    target = make_token(
        department_id="dep_a", service_roles={}, user_id="usr_grantee",
    )
    await client.post(
        f"{_API}/server-accounts/{account_a.id}/acl",
        headers=_auth(admin_token),
        json={"user_id": "usr_grantee", "actions": ["view"]},
    )
    assert (await client.get(
        f"{_API}/server-accounts/{account_a.id}", headers=_auth(target),
    )).status_code == 200

    d = await client.delete(
        f"{_API}/server-accounts/{account_a.id}/acl/usr_grantee",
        headers=_auth(admin_token),
    )
    assert d.status_code == 200
    assert (await client.get(
        f"{_API}/server-accounts/{account_a.id}", headers=_auth(target),
    )).status_code == 403


async def test_grant_update_allows_patch(client, admin_token, make_token, account_a):
    target = make_token(
        department_id="dep_a", service_roles={}, user_id="usr_upd",
    )
    await client.post(
        f"{_API}/server-accounts/{account_a.id}/acl",
        headers=_auth(admin_token),
        json={"user_id": "usr_upd", "actions": ["update"]},
    )
    r = await client.patch(
        f"{_API}/server-accounts/{account_a.id}",
        headers=_auth(target),
        json={"shell": "/bin/zsh"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["shell"] == "/bin/zsh"


async def test_grant_delete_allows_delete(client, admin_token, make_token, account_a):
    target = make_token(
        department_id="dep_a", service_roles={}, user_id="usr_del",
    )
    await client.post(
        f"{_API}/server-accounts/{account_a.id}/acl",
        headers=_auth(admin_token),
        json={"user_id": "usr_del", "actions": ["delete"]},
    )
    r = await client.delete(
        f"{_API}/server-accounts/{account_a.id}", headers=_auth(target),
    )
    assert r.status_code == 200


async def test_view_grant_does_not_leak_password(
    client, admin_token, make_token, account_a,
):
    """Только view → карточка видна, пароль скрыт."""
    target = make_token(
        department_id="dep_a", service_roles={}, user_id="usr_v",
    )
    await client.post(
        f"{_API}/server-accounts/{account_a.id}/acl",
        headers=_auth(admin_token),
        json={"user_id": "usr_v", "actions": ["view"]},
    )
    r = await client.get(
        f"{_API}/server-accounts/{account_a.id}", headers=_auth(target),
    )
    assert r.status_code == 200
    assert r.json()["password_b64"] is None


# ── department_admin bypass ─────────────────────────────────────────────────


async def test_department_admin_bypass_can_view_password(
    client, make_token, account_a,
):
    """department_admin своего отдела видит пароль без явного гранта/роли."""
    dep_admin = make_token(
        platform_role="department_admin",
        department_id="dep_a",
        service_roles={},
        user_id="usr_depadmin",
    )
    r = await client.get(
        f"{_API}/server-accounts/{account_a.id}", headers=_auth(dep_admin),
    )
    assert r.status_code == 200
    assert r.json()["password_b64"] is not None
