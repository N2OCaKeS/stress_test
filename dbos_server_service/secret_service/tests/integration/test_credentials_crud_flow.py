"""CRUD happy-paths для credentials через реальный HTTP-стек.

Покрытие — README §«Credentials CRUD»: POST → GET → PATCH → DELETE → GET=404,
вариации personal / department / cross_department, info-leak protection при
cross-dep запросах без grant'ов, и audit-эмиссия CRUD-событий.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import auth_header

BASE = "/api/secret/v1"

pytestmark = pytest.mark.asyncio


# ── personal ───────────────────────────────────────────────────────────────


async def test_personal_credential_full_lifecycle(
    client, identity_factory, mock_logging_service,
):
    """personal: POST → 201; GET → meta; PATCH → updated; DELETE → 200; GET → 404."""
    owner = identity_factory(
        user_id="usr_personal_owner_a",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )

    create = await client.post(
        f"{BASE}/credentials",
        headers=auth_header(owner),
        json={
            "name": "jira_personal",
            "service": "jira",
            "scope": "personal",
            "login": "alice",
            "secret": "alice-secret-v1",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    cred_id = body["id"]
    assert body["scope"] == "personal"
    assert body["owner_user_id"] == "usr_personal_owner_a"
    assert body["owner_dept_id"] is None
    assert body["status"] == "active"
    # Plaintext не утекает в meta.
    assert "secret" not in body
    assert "secret_encrypted" not in body

    get_resp = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(owner))
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "jira_personal"

    patch = await client.patch(
        f"{BASE}/credentials/{cred_id}",
        headers=auth_header(owner),
        json={"login": "alice@new", "secret": "alice-secret-v2"},
    )
    assert patch.status_code == 200
    assert patch.json()["login"] == "alice@new"

    delete = await client.delete(f"{BASE}/credentials/{cred_id}", headers=auth_header(owner))
    assert delete.status_code == 200
    assert delete.json()["ok"] is True

    gone = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(owner))
    assert gone.status_code == 404
    assert gone.json()["error_code"] == "CREDENTIAL_NOT_FOUND"

    # Audit: create/update/delete присутствуют.
    actions = {e["action"] for e in mock_logging_service}
    assert {"tokens.create", "tokens.update", "tokens.delete"} <= actions


async def test_department_credential_lifecycle_by_dep_admin(
    client, identity_factory, mock_logging_service,
):
    """department: dep_admin своего dep'а создаёт/удаляет; владелец = dep."""
    admin = identity_factory(
        user_id="usr_dep_admin_a",
        department_id="dep_a",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )

    create = await client.post(
        f"{BASE}/credentials",
        headers=auth_header(admin),
        json={
            "name": "dept_jira_bot",
            "service": "jira",
            "scope": "department",
            "secret": "dept-shared-secret",
            "owner_dept_id": "dep_a",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    cred_id = body["id"]
    assert body["scope"] == "department"
    assert body["owner_dept_id"] == "dep_a"
    assert body["owner_user_id"] is None

    delete = await client.delete(f"{BASE}/credentials/{cred_id}", headers=auth_header(admin))
    assert delete.status_code == 200

    create_action = mock_logging_service.find_one("tokens.create")
    assert create_action is not None
    assert create_action["details"]["scope"] == "department"


async def test_cross_dep_creation_invisible_to_unrelated_dept_returns_404(
    client, identity_factory,
):
    """cross_department: actor в чужом dep'е без DeptGrant'а видит 404, не 403.
    Это info-leak protection: сам факт существования cred'ы не светим."""
    owner_admin = identity_factory(
        user_id="usr_owner_admin",
        department_id="dep_b",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )

    create = await client.post(
        f"{BASE}/credentials",
        headers=auth_header(owner_admin),
        json={
            "name": "shared_mirror",
            "service": "git",
            "scope": "cross_department",
            "secret": "mirror-secret",
            "owner_dept_id": "dep_b",
        },
    )
    assert create.status_code == 201, create.text
    cred_id = create.json()["id"]

    foreign_reader = identity_factory(
        user_id="usr_foreign_reader",
        department_id="dep_a",
        service_roles={"secret_service": ["reader"]},
    )
    resp = await client.get(
        f"{BASE}/credentials/{cred_id}",
        headers=auth_header(foreign_reader),
    )
    # 404 — info-leak protection, не 403.
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "CREDENTIAL_NOT_FOUND"


async def test_create_duplicate_name_409(client, identity_factory):
    """Повторный POST с тем же `(owner, service, name)` → 409 NAME_DUPLICATE."""
    owner = identity_factory(
        user_id="usr_dup_owner",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    payload = {
        "name": "duped",
        "service": "jira",
        "scope": "personal",
        "secret": "x",
    }
    first = await client.post(f"{BASE}/credentials", headers=auth_header(owner), json=payload)
    assert first.status_code == 201

    second = await client.post(f"{BASE}/credentials", headers=auth_header(owner), json=payload)
    assert second.status_code == 409
    assert second.json()["error_code"] == "NAME_DUPLICATE"


async def test_list_credentials_returns_only_visible(client, identity_factory, cred_factory):
    """GET /credentials видит только собственные personal + cred'ы своего dep'а."""
    await cred_factory(
        owner_user_id="usr_listing_owner",
        scope="personal",
        name="own_personal",
    )
    # Чужая personal cred — не должна попадать в список.
    await cred_factory(
        owner_user_id="usr_someone_else",
        scope="personal",
        name="foreign_personal",
    )

    token = identity_factory(
        user_id="usr_listing_owner",
        department_id="dep_listing",
        service_roles={"secret_service": ["operator"]},
    )
    resp = await client.get(f"{BASE}/credentials", headers=auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    names = {item["name"] for item in body["items"]}
    assert "own_personal" in names
    assert "foreign_personal" not in names
