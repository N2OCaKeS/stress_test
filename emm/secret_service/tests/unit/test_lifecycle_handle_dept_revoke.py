"""`lifecycle_service.handle_dept_service_access_revoked` — cascade без owner-кред."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.repositories import role_acls as acls_repo
from src.services import lifecycle_service


DEPT = "dep_revoked0000000000000000000a1"
OWNER_DEPT = "dep_owner000000000000000000000a1"
OTHER_DEPT = "dep_other000000000000000000000a1"
ACTOR = "usr_admin000000000000000000000a1"


async def _cred(adb, *, cred_id: str, scope: str, owner_user_id=None, owner_dept_id=None):
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name=f"n_{cred_id[-4:]}",
        service="jira",
        scope=scope,
        owner_user_id=owner_user_id,
        owner_dept_id=owner_dept_id,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by=ACTOR,
    )
    await adb.commit()
    return cred


async def _grant(adb, cred_id: str, recipient: str, grant_id: str):
    g = await grants_repo.create(
        adb, id=grant_id, cred_id=cred_id, recipient_dept_id=recipient,
        granted_by_user_id=ACTOR,
    )
    await adb.commit()
    return g


async def _acl(adb, *, acl_id: str, cred_id: str, dept_id: str):
    a = await acls_repo.create(
        adb,
        id=acl_id,
        cred_id=cred_id,
        dept_id=dept_id,
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id=ACTOR,
    )
    await adb.commit()
    return a


@pytest.mark.asyncio
async def test_dept_revoke_cascade_drops_grants_and_acls(adb):
    """Sn'имает все DeptGrant'ы где dep — recipient + RoleACL'и на этом dep'е.

    Personal-cred с прямым ACL на dep тоже теряет этот ACL.
    """
    cross = await _cred(
        adb, cred_id="cred_lr_a01", scope="cross_department", owner_dept_id=OWNER_DEPT,
    )
    await _grant(adb, cross.id, DEPT, "dgr_lr_a01")
    await _acl(adb, acl_id="acl_lr_a01", cred_id=cross.id, dept_id=DEPT)

    # Personal-cred другого user'а с прямым ACL на DEPT.
    personal = await _cred(
        adb, cred_id="cred_lr_a02", scope="personal",
        owner_user_id="usr_owner000000000000000000000ff",
    )
    await _acl(adb, acl_id="acl_lr_a02", cred_id=personal.id, dept_id=DEPT)

    # Соседний dep не пострадает.
    await _grant(adb, cross.id, OTHER_DEPT, "dgr_lr_a02")
    await _acl(adb, acl_id="acl_lr_a03", cred_id=cross.id, dept_id=OTHER_DEPT)

    emitted = []
    with patch.object(
        lifecycle_service.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    ):
        summary = await lifecycle_service.handle_dept_service_access_revoked(
            adb, DEPT, "secret_service", ACTOR,
        )

    assert summary["errors"] == []
    assert summary["dept_grants_revoked"] == 1
    # Два ACL у DEPT (один в cross, один в personal) — оба должны уйти.
    assert summary["role_acls_revoked"] == 2

    assert await grants_repo.find(adb, cross.id, DEPT) is None
    assert await grants_repo.find(adb, cross.id, OTHER_DEPT) is not None

    cross_acls = await acls_repo.get_for_cred(adb, cross.id)
    assert {a.dept_id for a in cross_acls} == {OTHER_DEPT}
    personal_acls = await acls_repo.get_for_cred(adb, personal.id)
    assert personal_acls == []

    assert any(a == "tokens.dept_revoke_cascade" for a, _ in emitted)


@pytest.mark.asyncio
async def test_dept_revoke_other_service_noop(adb):
    """Событие про чужой сервис — игнорируем."""
    cross = await _cred(
        adb, cred_id="cred_lr_b01", scope="cross_department", owner_dept_id=OWNER_DEPT,
    )
    await _grant(adb, cross.id, DEPT, "dgr_lr_b01")

    summary = await lifecycle_service.handle_dept_service_access_revoked(
        adb, DEPT, "logging_service", ACTOR,
    )
    assert summary == {"dept_grants_revoked": 0, "role_acls_revoked": 0, "errors": []}
    # Grant остался на месте.
    assert await grants_repo.find(adb, cross.id, DEPT) is not None


@pytest.mark.asyncio
async def test_dept_revoke_leaves_owned_creds_alone(adb):
    """Свои cred'ы dep'а (owner_dept_id=DEPT) — handle_dept_deleted_as_owner, не наш кейс."""
    owned = await _cred(
        adb, cred_id="cred_lr_c01", scope="department", owner_dept_id=DEPT,
    )
    await lifecycle_service.handle_dept_service_access_revoked(
        adb, DEPT, "secret_service", ACTOR,
    )
    refreshed = await cred_repo.get_by_id(adb, owned.id)
    assert refreshed is not None
    assert refreshed.status == "active"
