"""Доступ к personal cred'е: owner vs другие user'ы vs боты vs чужой dep.

README §«Зоны видимости / personal» + §«Bot и PAT»:
* владелец user_a — read + reveal всегда;
* user_b в том же dep'е без RoleACL — 404 (info-leak protection);
* user_b в том же dep'е с RoleACL(can_read=True) — read + reveal ok;
* user_c в чужом dep'е — 404 даже с RoleACL (RoleACL не выдан в его dep'е);
* bot dep_a — НЕ видит personal user_a (у бота нет user-identity).
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import auth_header
from tests._helpers import b64

BASE = "/api/secret/v1"

pytestmark = pytest.mark.asyncio


async def _create_personal_for(client, identity_factory, *, user_id, dept_id):
    owner = identity_factory(
        user_id=user_id,
        department_id=dept_id,
        service_roles={"secret_service": ["operator"]},
    )
    resp = await client.post(
        f"{BASE}/credentials",
        headers=auth_header(owner),
        json={
            "name": f"per_{user_id[-6:]}",
            "service": "jira",
            "scope": "personal",
            "login": "alice",
            "secret_b64": b64("alice-secret"),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], owner


async def test_owner_can_read_and_reveal(client, identity_factory):
    cred_id, owner = await _create_personal_for(
        client, identity_factory,
        user_id="usr_owner_a", dept_id="dep_a",
    )

    read = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(owner))
    assert read.status_code == 200

    reveal = await client.post(f"{BASE}/credentials/{cred_id}/reveal", headers=auth_header(owner))
    assert reveal.status_code == 200
    body = reveal.json()
    assert body["login"] == "alice"
    # secret_b64 — base64(plaintext).
    import base64
    assert base64.b64decode(body["secret_b64"]).decode() == "alice-secret"


async def test_same_dep_user_without_acl_gets_404(client, identity_factory):
    cred_id, _ = await _create_personal_for(
        client, identity_factory,
        user_id="usr_owner_a2", dept_id="dep_a",
    )

    other = identity_factory(
        user_id="usr_other_a",
        department_id="dep_a",
        service_roles={"secret_service": ["reader"]},
    )
    resp = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(other))
    # Тот же dep, но без ACL и не owner — 404 (не светим существование).
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CREDENTIAL_NOT_FOUND"


async def test_same_dep_user_with_acl_can_reveal(
    client, identity_factory, mock_logging_service,
):
    cred_id, owner = await _create_personal_for(
        client, identity_factory,
        user_id="usr_owner_a3", dept_id="dep_a",
    )

    # Владелец выдаёт RoleACL(reader, can_read=True) в своём dep'е.
    acl_resp = await client.post(
        f"{BASE}/credentials/{cred_id}/acl",
        headers=auth_header(owner),
        json={
            "dept_id": "dep_a",
            "role_name": "reader",
            "can_read": True,
            "can_write": False,
        },
    )
    assert acl_resp.status_code == 201, acl_resp.text

    grantee = identity_factory(
        user_id="usr_grantee",
        department_id="dep_a",
        service_roles={"secret_service": ["reader"]},
    )
    reveal = await client.post(
        f"{BASE}/credentials/{cred_id}/reveal", headers=auth_header(grantee),
    )
    assert reveal.status_code == 200
    assert reveal.json()["login"] == "alice"

    # Один из событий должен быть tokens.role_acl_added.
    assert mock_logging_service.find_one("tokens.role_acl_added") is not None


async def test_foreign_dep_user_with_acl_in_their_own_dep_still_404(
    client, identity_factory,
):
    """RoleACL выдан только в dep_a; user_c в dep_b не видит cred даже если в его
    dep'е есть совпадающее role_name."""
    cred_id, owner = await _create_personal_for(
        client, identity_factory,
        user_id="usr_owner_a4", dept_id="dep_a",
    )
    await client.post(
        f"{BASE}/credentials/{cred_id}/acl",
        headers=auth_header(owner),
        json={"dept_id": "dep_a", "role_name": "reader", "can_read": True},
    )

    foreign = identity_factory(
        user_id="usr_foreign_b",
        department_id="dep_b",
        service_roles={"secret_service": ["reader"]},
    )
    resp = await client.get(
        f"{BASE}/credentials/{cred_id}", headers=auth_header(foreign),
    )
    assert resp.status_code == 404


async def test_bot_does_not_see_personal_creds(client, identity_factory):
    """Bot dep_a — нет user-identity. README §«Bot и PAT»: personal cred'ы
    другого user'а не видны."""
    cred_id, _ = await _create_personal_for(
        client, identity_factory,
        user_id="usr_owner_for_bot", dept_id="dep_a",
    )

    bot = identity_factory(
        user_id="bot_dep_a",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
        subject_type="bot",
    )
    resp = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(bot))
    assert resp.status_code == 404
