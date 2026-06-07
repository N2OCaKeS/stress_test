"""`lifecycle_service.handle_dept_deleted_*` — owner-side blocked, recipient cascade."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.repositories import role_acls as acls_repo
from src.services import lifecycle_service


DEPT_DELETED = "dep_deleted00000000000000000000a1"
OWNER_DEPT = "dep_owner000000000000000000000a1"
OTHER_DEPT = "dep_other000000000000000000000a1"
ACTOR = "usr_admin000000000000000000000a1"


async def _make_dept_cred(adb, *, cred_id: str, dept_id: str, scope: str = "department"):
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name=f"n_{cred_id[-4:]}",
        service="jira",
        scope=scope,
        owner_user_id=None,
        owner_dept_id=dept_id,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by=ACTOR,
    )
    await adb.commit()
    return cred


async def _add_grant(adb, cred_id: str, recipient_dept_id: str, grant_id: str):
    grant = await grants_repo.create(
        adb,
        id=grant_id,
        cred_id=cred_id,
        recipient_dept_id=recipient_dept_id,
        granted_by_user_id=ACTOR,
    )
    await adb.commit()
    return grant


async def _add_acl(adb, *, acl_id: str, cred_id: str, dept_id: str, role: str = "reader"):
    acl = await acls_repo.create(
        adb,
        id=acl_id,
        cred_id=cred_id,
        dept_id=dept_id,
        role_name=role,
        can_read=True,
        can_write=False,
        granted_by_user_id=ACTOR,
    )
    await adb.commit()
    return acl


# ── as_owner ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dept_deleted_owner_blocks_creds(adb):
    cred_a = await _make_dept_cred(adb, cred_id="cred_ld_a01", dept_id=DEPT_DELETED)
    cred_b = await _make_dept_cred(
        adb, cred_id="cred_ld_a02", dept_id=DEPT_DELETED, scope="cross_department",
    )
    cred_other = await _make_dept_cred(
        adb, cred_id="cred_ld_a03", dept_id=OTHER_DEPT,
    )

    emitted = []
    with patch.object(
        lifecycle_service.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    ):
        summary = await lifecycle_service.handle_dept_deleted_as_owner(
            adb, DEPT_DELETED, ACTOR,
        )

    assert summary["blocked_count"] == 2
    assert summary["errors"] == []
    assert (await cred_repo.get_by_id(adb, cred_a.id)).status == "blocked"
    assert (await cred_repo.get_by_id(adb, cred_b.id)).status == "blocked"
    assert (await cred_repo.get_by_id(adb, cred_other.id)).status == "active"
    actions = [a for a, _ in emitted]
    assert actions.count("tokens.owner_dept_deleted_block") == 2


@pytest.mark.asyncio
async def test_dept_deleted_owner_idempotent(adb):
    """Повторный вызов на уже blocked-кред не плодит дубль аудита."""
    cred = await _make_dept_cred(adb, cred_id="cred_ld_b01", dept_id=DEPT_DELETED)
    await cred_repo.mark_blocked(adb, cred, reason="owner_dept_deleted")
    await adb.commit()

    emitted = []
    with patch.object(
        lifecycle_service.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    ):
        summary = await lifecycle_service.handle_dept_deleted_as_owner(
            adb, DEPT_DELETED, ACTOR,
        )
    assert summary["blocked_count"] == 0
    assert not any(a == "tokens.owner_dept_deleted_block" for a, _ in emitted)


# ── as_recipient ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dept_deleted_recipient_cascades(adb):
    """DeptGrant(_, deleted_dept) + RoleACL'и для deleted_dept'а исчезают."""
    cred = await _make_dept_cred(
        adb, cred_id="cred_ld_c01", dept_id=OWNER_DEPT, scope="cross_department",
    )
    await _add_grant(adb, cred.id, DEPT_DELETED, "dgr_ld_c01")
    await _add_acl(adb, acl_id="acl_ld_c01", cred_id=cred.id, dept_id=DEPT_DELETED)
    # Соседний grant другого dep'а не должен пострадать.
    await _add_grant(adb, cred.id, OTHER_DEPT, "dgr_ld_c02")
    await _add_acl(adb, acl_id="acl_ld_c02", cred_id=cred.id, dept_id=OTHER_DEPT)

    emitted = []
    with patch.object(
        lifecycle_service.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    ):
        summary = await lifecycle_service.handle_dept_deleted_as_recipient(
            adb, DEPT_DELETED, ACTOR,
        )

    assert summary["dept_grants_revoked"] == 1
    assert summary["role_acls_revoked"] == 1
    assert summary["errors"] == []

    # Только удалённый dep потерял grant и ACL.
    assert await grants_repo.find(adb, cred.id, DEPT_DELETED) is None
    assert await grants_repo.find(adb, cred.id, OTHER_DEPT) is not None
    remaining_acls = await acls_repo.get_for_cred(adb, cred.id)
    assert {a.dept_id for a in remaining_acls} == {OTHER_DEPT}

    assert any(a == "tokens.dept_recipient_cascade" for a, _ in emitted)


@pytest.mark.asyncio
async def test_dept_deleted_recipient_noop_if_no_grants(adb):
    summary = await lifecycle_service.handle_dept_deleted_as_recipient(
        adb, DEPT_DELETED, ACTOR,
    )
    assert summary["dept_grants_revoked"] == 0
    assert summary["role_acls_revoked"] == 0
    assert summary["errors"] == []
