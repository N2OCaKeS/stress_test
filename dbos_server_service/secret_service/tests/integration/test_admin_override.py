"""Admin override flows: admin secret_service'а своего dept'а.

README §«Service admin»:
* admin своего dept'а DELETE без reason → 422 ADMIN_OVERRIDE_REASON_REQUIRED.
* admin своего dept'а DELETE с reason → 200 + CRITICAL `tokens.admin_override_delete`.
* admin своего dept'а READ blocked cred → 200 (для аудита).
* admin ЧУЖОГО dept'а → 404 CREDENTIAL_NOT_FOUND (cross-dept privileges нет).
* account_admin transfer на blocked cross_dep cred → 403 CREDENTIAL_ACCESS_DENIED
  (платформенный админ не имеет доступа к содержимому секретов; восстановление
  владельца идёт через admin secret_service'а владеющего dept'а).
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import auth_header
from tests._helpers import b64

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
        json={"name": "to_be_overridden", "service": "jira", "scope": "personal", "secret_b64": b64("x")},
    )
    cred_id = cred.json()["id"]

    # admin per-(dept, service) — должен сидеть в том же dept'е, что и владелец cred'ы.
    svc_admin = identity_factory(
        user_id="usr_svc_admin",
        department_id="dep_a",
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
        json={"name": "override_target", "service": "jira", "scope": "personal", "secret_b64": b64("x")},
    )
    cred_id = cred.json()["id"]

    svc_admin = identity_factory(
        user_id="usr_svc_admin2",
        department_id="dep_a",
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
        json={"name": "to_block", "service": "jira", "scope": "personal", "secret_b64": b64("x")},
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
    # admin своего dept'а — 200 с blocked-метой.
    svc_admin = identity_factory(
        user_id="usr_svc_admin3",
        department_id="dep_a",
        service_roles={"secret_service": ["admin"]},
    )
    get_resp = await client.get(
        f"{BASE}/credentials/{cred_id}", headers=auth_header(svc_admin),
    )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["status"] == "blocked"
    assert body["blocked_reason"] == "owner_user_deleted"


async def test_service_admin_cannot_delete_cred_in_other_dept(
    client, identity_factory,
):
    """admin secret_service'а dep_a НЕ имеет прав над personal-cred'ой dep_b.

    Регрессия на cleanup модели ролей: раньше admin был cross-dept (read /
    delete override любой cred'ы). Теперь роль per-(dept, service), и admin
    dep_a, дергая endpoint dep_b'шной cred'ы, видит 404 — endpoint
    защищается тем же visibility-фильтром, что и обычный actor.
    """
    owner = identity_factory(
        user_id="usr_owner_z",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    cred = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "off_limits", "service": "jira", "scope": "personal", "secret_b64": b64("x")},
    )
    cred_id = cred.json()["id"]

    # admin живёт в dep_b — должен получить 404 / 403.
    foreign_admin = identity_factory(
        user_id="usr_foreign_admin",
        department_id="dep_b",
        service_roles={"secret_service": ["admin"]},
    )

    resp = await client.request(
        "DELETE",
        f"{BASE}/credentials/{cred_id}",
        headers=auth_header(foreign_admin),
        json={"reason": "should not be allowed"},
    )
    # Cross-dept visibility-miss → 404 (info leak protection); 403 тоже
    # приемлем, если запрос дошёл до access_service.check_access.
    assert resp.status_code in (403, 404), resp.text
    assert resp.json().get("error_code") in {
        "CREDENTIAL_NOT_FOUND",
        "CREDENTIAL_ACCESS_DENIED",
    }

    get_resp = await client.get(
        f"{BASE}/credentials/{cred_id}", headers=auth_header(foreign_admin),
    )
    assert get_resp.status_code in (403, 404)


async def test_account_admin_cannot_transfer_cross_dep_cred(
    client, identity_factory, mock_logging_service,
):
    """account_admin к transfer не подпущен: платформенный админ не имеет
    доступа к содержимому секретов. После delete owner_dep'а восстановление
    идёт через admin secret_service'а другого dept'а (если он был recipient'ом)
    либо через lifecycle/sweep."""
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
            "secret_b64": b64("x"),
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

    # account_admin пробует transfer на другой dep — отбиваемся 403.
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
    assert transfer.status_code == 403, transfer.text
    assert transfer.json()["error_code"] == "CREDENTIAL_ACCESS_DENIED"

    # success-варианта transfer'а быть не должно; failure-вариант — должен
    # (P0-7: на denied access audit обязан получить tokens.transfer_ownership/failure).
    transfer_events = mock_logging_service.by_action("tokens.transfer_ownership")
    assert all(e["status"] == "failure" for e in transfer_events), (
        f"unexpected success transfer event: {transfer_events}"
    )
    assert any(e["status"] == "failure" for e in transfer_events), (
        f"missing failure event for denied transfer: {transfer_events}"
    )
