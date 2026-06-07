"""`lifecycle_service.handle_user_deleted` — personal cred'ы удалённого user'а."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.repositories import credentials as cred_repo
from src.repositories import role_acls as acls_repo
from src.services import lifecycle_service


USER_ID = "usr_alice0000000000000000000000a1"
ACTOR_ID = "usr_admin0000000000000000000000a1"
OTHER_USER = "usr_bob000000000000000000000000a1"


async def _make_personal(adb, *, cred_id: str, name: str = "jira_p", user_id: str = USER_ID):
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name=name,
        service="jira",
        scope="personal",
        owner_user_id=user_id,
        owner_dept_id=None,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by=user_id,
    )
    await adb.commit()
    return cred


async def _add_acl(adb, cred_id: str, dept_id: str = "dep_share00000000000000000000a1"):
    acl = await acls_repo.create(
        adb,
        id=f"acl_{cred_id[-6:]}_001",
        cred_id=cred_id,
        dept_id=dept_id,
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id=ACTOR_ID,
    )
    await adb.commit()
    return acl


@pytest.mark.asyncio
async def test_user_deleted_with_acl_blocks_cred(adb):
    cred = await _make_personal(adb, cred_id="cred_lu_a01")
    await _add_acl(adb, cred.id)

    emitted = []
    with patch.object(
        lifecycle_service.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    ):
        summary = await lifecycle_service.handle_user_deleted(adb, USER_ID, ACTOR_ID)

    assert summary == {"blocked_count": 1, "deleted_count": 0, "errors": []}
    refreshed = await cred_repo.get_by_id(adb, cred.id)
    assert refreshed is not None
    assert refreshed.status == "blocked"
    assert refreshed.blocked_reason == "owner_user_deleted"
    assert any(a == "tokens.owner_user_deleted_block" for a, _ in emitted)


@pytest.mark.asyncio
async def test_user_deleted_without_acl_hard_deletes(adb):
    cred = await _make_personal(adb, cred_id="cred_lu_b01", name="jira_orphan")

    emitted = []
    with patch.object(
        lifecycle_service.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    ):
        summary = await lifecycle_service.handle_user_deleted(adb, USER_ID, ACTOR_ID)

    assert summary == {"blocked_count": 0, "deleted_count": 1, "errors": []}
    gone = await cred_repo.get_by_id(adb, cred.id)
    assert gone is None
    deletes = [(a, kw) for a, kw in emitted if a == "tokens.delete"]
    assert len(deletes) == 1
    assert deletes[0][1]["details"]["auto_delete"] is True


@pytest.mark.asyncio
async def test_user_deleted_mixed_creds(adb):
    """Один cred с ACL → blocked; другой без ACL → deleted; чужой не трогаем."""
    cred_with = await _make_personal(adb, cred_id="cred_lu_c01", name="with")
    await _add_acl(adb, cred_with.id)
    cred_alone = await _make_personal(adb, cred_id="cred_lu_c02", name="alone")
    cred_other = await _make_personal(
        adb, cred_id="cred_lu_c03", name="other", user_id=OTHER_USER,
    )

    with patch.object(lifecycle_service.audit_service, "emit", side_effect=lambda *a, **k: None):
        summary = await lifecycle_service.handle_user_deleted(adb, USER_ID, ACTOR_ID)

    assert summary["blocked_count"] == 1
    assert summary["deleted_count"] == 1
    assert summary["errors"] == []
    assert (await cred_repo.get_by_id(adb, cred_with.id)).status == "blocked"
    assert (await cred_repo.get_by_id(adb, cred_alone.id)) is None
    assert (await cred_repo.get_by_id(adb, cred_other.id)).status == "active"


@pytest.mark.asyncio
async def test_user_deleted_collects_handler_errors(adb):
    """Repo-исключение → handler ловит, summary.errors заполнен, blocked_count=0."""
    cred = await _make_personal(adb, cred_id="cred_lu_d01")
    await _add_acl(adb, cred.id)

    async def _boom(*_a, **_k):
        raise RuntimeError("simulated repo failure")

    with patch.object(lifecycle_service.cred_repo, "mark_blocked", side_effect=_boom), \
         patch.object(lifecycle_service.audit_service, "emit", side_effect=lambda *a, **k: None):
        summary = await lifecycle_service.handle_user_deleted(adb, USER_ID, ACTOR_ID)

    assert summary["errors"]
    assert "simulated repo failure" in summary["errors"][0]
    assert summary["blocked_count"] == 0
