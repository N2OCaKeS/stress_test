"""Fixes: cross-dep ACL leak на personal-кред.

* `_check_personal` для не-владельца требует `acl.dept_id == owner_user_dept_id`.
* `role_acl_service.add` отбивает ACL на personal в чужом dep'е (422).
* Recipient cross-dep listing видит cred с DeptGrant+RoleACL.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import DomainValidationError
from src.dependencies.auth import Identity
from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.repositories import role_acls as acls_repo
from src.schemas.role_acls import RoleACLCreate
from src.services import access_service, credential_service, role_acl_service


def _identity(
    *,
    user_id: str = "usr_actor0001",
    department_id: str | None = "dep_actor0001",
    roles: list[str] | None = None,
    platform_role: str | None = None,
) -> Identity:
    return Identity(
        user_id=user_id,
        username="actor",
        actor_type="user",
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles or []},
        is_banned=False,
        platform_role=platform_role,
    )


@pytest.mark.asyncio
async def test_personal_cross_dep_acl_leak_blocked(adb) -> None:
    """Bob (dep_B) с RoleACL(cred, dep_B, can_read) НЕ читает personal Alice (dep_A)."""
    cred = await cred_repo.create(
        adb,
        id="cred_pleak01",
        name="leak01",
        service="jira",
        scope="personal",
        owner_user_id="usr_alice0001",
        owner_dept_id=None,
        owner_user_dept_id="dep_alice_a",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_alice0001",
    )
    # Тестируем поломанный ACL, оставшийся «исторически» — write-side gate
    # отдельный тест ниже отбивает создание таких ACL.
    await acls_repo.create(
        adb,
        id="acl_leak01",
        cred_id=cred.id,
        dept_id="dep_bob_b",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_alice0001",
    )
    await adb.commit()

    bob = _identity(user_id="usr_bob0001", department_id="dep_bob_b", roles=["reader"])
    allowed, reason = await access_service.check_access(adb, bob, cred, "read")
    assert not allowed
    assert reason == "scope_mismatch"


@pytest.mark.asyncio
async def test_personal_same_dep_acl_still_works(adb) -> None:
    """Регрессия: ACL в dep'е владельца по-прежнему даёт доступ другому user'у того же dep'а."""
    cred = await cred_repo.create(
        adb,
        id="cred_pleak02",
        name="leak02",
        service="jira",
        scope="personal",
        owner_user_id="usr_alice0002",
        owner_dept_id=None,
        owner_user_dept_id="dep_alice_a",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_alice0002",
    )
    await acls_repo.create(
        adb,
        id="acl_leak02",
        cred_id=cred.id,
        dept_id="dep_alice_a",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_alice0002",
    )
    await adb.commit()

    peer = _identity(
        user_id="usr_peer0002",
        department_id="dep_alice_a",
        roles=["reader"],
    )
    allowed, reason = await access_service.check_access(adb, peer, cred, "read")
    assert allowed
    # `read` (метаданные) — младший уровень лесенки; can_read его покрывает.
    assert reason == "acl_view"


@pytest.mark.asyncio
async def test_role_acl_add_rejects_foreign_dept_on_personal(adb) -> None:
    """role_acl_service.add: payload.dept_id != owner_user_dept_id → 422 PERSONAL_ACL_OWNER_DEPT_ONLY."""
    cred = await cred_repo.create(
        adb,
        id="cred_pleak03",
        name="leak03",
        service="jira",
        scope="personal",
        owner_user_id="usr_alice0003",
        owner_dept_id=None,
        owner_user_dept_id="dep_alice_a",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_alice0003",
    )
    await adb.commit()

    alice = _identity(user_id="usr_alice0003", department_id="dep_alice_a")
    payload = RoleACLCreate(
        dept_id="dep_bob_b", role_name="reader", can_read=True, can_write=False,
    )
    with pytest.raises(DomainValidationError) as exc:
        await role_acl_service.add(adb, alice, cred.id, payload)
    assert exc.value.error_code == "PERSONAL_ACL_OWNER_DEPT_ONLY"


@pytest.mark.asyncio
async def test_cross_dep_recipient_sees_cred_in_list(adb) -> None:
    """Recipient (dep_b) с DeptGrant+RoleACL видит cross-dep cred (owner=dep_a) в `list_visible`."""
    # Cred — owner dep_a.
    cred = await cred_repo.create(
        adb,
        id="cred_xlist01",
        name="xlist01",
        service="jira",
        scope="cross_department",
        owner_user_id=None,
        owner_dept_id="dep_owner_a",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_alice0010",
    )
    # DeptGrant на dep_b.
    await grants_repo.create(
        adb,
        id="dgr_xlist01",
        cred_id=cred.id,
        recipient_dept_id="dep_recipient_b",
        granted_by_user_id="usr_alice0010",
    )
    # ACL — reader в dep_b с can_read.
    await acls_repo.create(
        adb,
        id="acl_xlist01",
        cred_id=cred.id,
        dept_id="dep_recipient_b",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_alice0010",
    )
    await adb.commit()

    bob = _identity(
        user_id="usr_bob_x01",
        department_id="dep_recipient_b",
        roles=["reader"],
    )
    creds, _cursor = await credential_service.list_visible(
        adb,
        bob,
        scope=None,
        service=None,
        status=None,
        limit=20,
        cursor=None,
    )
    cred_ids = [c.id for c in creds]
    assert cred.id in cred_ids


@pytest.mark.asyncio
async def test_cross_dep_recipient_without_acl_does_not_see(adb) -> None:
    """Recipient без RoleACL — cred НЕ в списке (DeptGrant сам по себе не даёт read)."""
    cred = await cred_repo.create(
        adb,
        id="cred_xlist02",
        name="xlist02",
        service="jira",
        scope="cross_department",
        owner_user_id=None,
        owner_dept_id="dep_owner_a",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_alice0011",
    )
    await grants_repo.create(
        adb,
        id="dgr_xlist02",
        cred_id=cred.id,
        recipient_dept_id="dep_recipient_b2",
        granted_by_user_id="usr_alice0011",
    )
    await adb.commit()

    bob = _identity(
        user_id="usr_bob_x02",
        department_id="dep_recipient_b2",
        roles=["reader"],
    )
    creds, _cursor = await credential_service.list_visible(
        adb,
        bob,
        scope=None,
        service=None,
        status=None,
        limit=20,
        cursor=None,
    )
    assert all(c.id != cred.id for c in creds)
