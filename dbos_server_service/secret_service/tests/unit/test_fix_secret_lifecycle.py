"""Fixes: lifecycle idempotency + audit cred_ids cap."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.services import lifecycle_service


@pytest.mark.asyncio
async def test_handle_dept_deleted_as_recipient_idempotent(adb) -> None:
    """Повторный вызов без новых grant'ов — no-op, без падений."""
    summary1 = await lifecycle_service.handle_dept_deleted_as_recipient(
        adb, "dep_no_such_recipient", "usr_admin01"
    )
    assert summary1["dept_grants_revoked"] == 0
    assert summary1["role_acls_revoked"] == 0
    assert summary1["errors"] == []

    # Повтор без grant'ов — то же самое.
    summary2 = await lifecycle_service.handle_dept_deleted_as_recipient(
        adb, "dep_no_such_recipient", "usr_admin01"
    )
    assert summary2["dept_grants_revoked"] == 0
    assert summary2["errors"] == []


@pytest.mark.asyncio
async def test_dept_recipient_cascade_caps_cred_ids(adb) -> None:
    """>50 affected cred'ов → audit-details truncated + total_cred_ids."""
    # Создаём 60 cross-dep кред с DeptGrant на dep_b.
    for i in range(60):
        cid = f"cred_cap_{i:02d}"
        await cred_repo.create(
            adb,
            id=cid,
            name=cid,
            service="jira",
            scope="cross_department",
            owner_user_id=None,
            owner_dept_id="dep_owner_cap",
            login=None,
            secret_encrypted="v2$nonce$ct",
            status="active",
            created_by="usr_admin_cap",
        )
        await grants_repo.create(
            adb,
            id=f"dgr_cap_{i:02d}",
            cred_id=cid,
            recipient_dept_id="dep_recip_cap",
            granted_by_user_id="usr_admin_cap",
        )
    await adb.commit()

    emitted: list[dict] = []
    with patch.object(
        lifecycle_service.audit_service,
        "emit",
        side_effect=lambda action, **kw: emitted.append({"action": action, **kw}),
    ):
        summary = await lifecycle_service.handle_dept_deleted_as_recipient(
            adb, "dep_recip_cap", "usr_admin_cap"
        )

    assert summary["dept_grants_revoked"] == 60
    cascade = [e for e in emitted if e["action"] == "tokens.dept_recipient_cascade"]
    assert len(cascade) == 1
    details = cascade[0]["details"]
    assert len(details["cred_ids"]) == 50
    assert details.get("truncated") is True
    assert details.get("total_cred_ids") == 60
