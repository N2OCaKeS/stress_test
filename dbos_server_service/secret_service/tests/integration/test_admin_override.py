"""Admin override flows: service_admin (DELETE/READ blocked) + account_admin (transfer).

README §«Service admin» / §«Account admin»:
* service_admin DELETE без reason → 422 ADMIN_OVERRIDE_REASON_REQUIRED.
* service_admin DELETE с reason → 200 + CRITICAL `tokens.admin_override_delete`.
* service_admin READ blocked cred → 200 (для аудита).
* account_admin transfer на blocked cross_dep cred → 200 + transfer_ownership.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import auth_header

BASE = "/api/secret/v1"
INTERNAL_KEY = "internal-test-key"

pytestmark = pytest.mark.asyncio


async def test_service_admin_delete_without_reason_422(
    client, identity_factory,
):
    owner = identity_factory(
        user_id="usr_owner_x",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    cred = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "to_be_overridden", "service": "jira", "scope": "personal", "secret": "x"},
    )
    cred_id = cred.json()["id"]

    svc_admin = identity_factory(
        user_id="usr_svc_admin",
        department_id="dep_b",  # сам в другом dep'е — переопределяет чужой personal
        service_roles={"secret_service": ["admin"]},
    )

    resp = await client.delete(
        f"{BASE}/credentials/{cred_id}", headers=auth_header(svc_admin),
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "ADMIN_OVERRIDE_REASON_REQUIRED"


async def test_service_admin_delete_with_reason_succeeds_critical(
    client, identity_factory, mock_logging_service,
):
    owner = identity_factory(
        user_id="usr_owner_y",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    cred = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "override_target", "service": "jira", "scope": "personal", "secret": "x"},
    )
    cred_id = cred.json()["id"]

    svc_admin = identity_factory(
        user_id="usr_svc_admin2",
        department_id="dep_b",
        service_roles={"secret_service": ["admin"]},
    )

    resp = await client.request(
        "DELETE",
        f"{BASE}/credentials/{cred_id}",
        headers=auth_header(svc_admin),
        json={"reason": "security incident #42"},
    )
    assert resp.status_code == 200, resp.text

    event = mock_logging_service.find_one("tokens.admin_override_delete")
    assert event is not None
    assert event["details"]["reason"] == "security incident #42"


async def test_service_admin_can_read_blocked_for_audit(
    client, identity_factory,
):
    """service_admin читает blocked cred → 200 (для аудита)."""
    # Создаём cred owner-ом и блокируем через lifecycle.
    owner_id = "usr_blocked_owner"
    owner = identity_factory(
        user_id=owner_id,
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    cred = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "to_block", "service": "jira", "scope": "personal", "secret": "x"},
    )
    cred_id = cred.json()["id"]
    # Выдаём ACL, чтобы lifecycle блокировал, а не удалял.
    acl = await client.post(
        f"{BASE}/credentials/{cred_id}/acl",
        headers=auth_header(owner),
        json={"dept_id": "dep_a", "role_name": "reader", "can_read": True},
    )
    assert acl.status_code == 201

    resp = await client.post(
        f"{BASE}/internal/lifecycle/user-deleted",
        headers={
            "Authorization": f"Bearer {INTERNAL_KEY}",
            "X-Service-Identity": "auth_service",
        },
        json={"user_id": owner_id, "actor_id": "usr_admin"},
    )
    assert resp.status_code == 200

    # Не-admin reader получит 410 GONE (или 404, если нет ACL).
    # service_admin — 200 c blocked-метой.
    svc_admin = identity_factory(
        user_id="usr_svc_admin3",
        department_id="dep_b",
        service_roles={"secret_service": ["admin"]},
    )
    get_resp = await client.get(
        f"{BASE}/credentials/{cred_id}", headers=auth_header(svc_admin),
    )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["status"] == "blocked"
    assert body["blocked_reason"] == "owner_user_deleted"


async def test_account_admin_transfer_blocked_cross_dep_cred(
    client, identity_factory, mock_logging_service,
):
    """account_admin делает transfer ownership cross_dep cred'е после блокировки."""
    owner_admin = identity_factory(
        user_id="usr_owner_admin",
        department_id="dep_owner_t",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    create = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner_admin),
        json={
            "name": "transfer_target",
            "service": "git",
            "scope": "cross_department",
            "secret": "x",
            "owner_dept_id": "dep_owner_t",
        },
    )
    assert create.status_code == 201, create.text
    cred_id = create.json()["id"]

    # Блокируем owner dep через lifecycle.
    resp = await client.post(
        f"{BASE}/internal/lifecycle/dept-deleted",
        headers={
            "Authorization": f"Bearer {INTERNAL_KEY}",
            "X-Service-Identity": "auth_service",
        },
        json={"dept_id": "dep_owner_t", "actor_id": "usr_account_admin"},
    )
    assert resp.status_code == 200

    # account_admin делает transfer на другой dep.
    account_admin = identity_factory(
        user_id="usr_account_admin",
        department_id=None,
        platform_role="account_admin",
        service_roles={},
        allowed_services=["secret_service"],
    )
    transfer = await client.post(
        f"{BASE}/credentials/{cred_id}/transfer",
        headers=auth_header(account_admin),
        json={"new_owner_dept_id": "dep_new_owner", "reason": "owner dept dissolved"},
    )
    assert transfer.status_code == 200, transfer.text
    body = transfer.json()
    assert body["owner_dept_id"] == "dep_new_owner"
    assert body["status"] == "active"

    assert mock_logging_service.find_one("tokens.transfer_ownership") is not None
