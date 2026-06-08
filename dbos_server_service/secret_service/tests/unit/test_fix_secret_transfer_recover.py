"""Fixes: transfer/recover FOR UPDATE + IntegrityError → 409 + reason.

Покрывает:
* `TransferRequest.reason` теперь обязателен.
* audit `tokens.transfer_ownership` несёт `old_owner_user_id/old_owner_dept_id/reason`.
* IntegrityError на partial UNIQUE → ConflictError 409 (transfer и recover).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ConflictError
from src.dependencies.auth import Identity
from src.repositories import credentials as cred_repo
from src.schemas.credentials import TransferRequest
from src.services import credential_service


def _admin(user_id: str = "usr_svc_admin01") -> Identity:
    return Identity(
        user_id=user_id,
        username="adm",
        actor_type="user",
        department_id="dep_admin01",
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["admin"]},
        is_banned=False,
        platform_role=None,
    )


@pytest.mark.asyncio
async def test_transfer_request_requires_reason() -> None:
    with pytest.raises(ValidationError):
        TransferRequest(new_owner_user_id="usr_new00001")
    # Reason пустой — тоже валится.
    with pytest.raises(ValidationError):
        TransferRequest(new_owner_user_id="usr_new00001", reason="")
    # Корректный payload.
    req = TransferRequest(new_owner_user_id="usr_new00001", reason="owner left org")
    assert req.reason == "owner left org"


@pytest.mark.asyncio
async def test_transfer_audit_includes_old_owner_and_reason(adb) -> None:
    cred = await cred_repo.create(
        adb,
        id="cred_trfix01",
        name="trfix01",
        service="jira",
        scope="personal",
        owner_user_id="usr_oldowner001",
        owner_dept_id=None,
        owner_user_dept_id="dep_oldowner01",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_user_deleted",
        created_by="usr_oldowner001",
    )
    await adb.commit()

    payload = TransferRequest(new_owner_user_id="usr_newowner01", reason="moved to other team")

    emitted: list[dict] = []
    with patch.object(
        credential_service.audit_service,
        "emit",
        side_effect=lambda action, **kw: emitted.append({"action": action, **kw}),
    ):
        await credential_service.transfer(adb, _admin(), cred.id, payload)

    transfer_events = [e for e in emitted if e["action"] == "tokens.transfer_ownership"]
    assert len(transfer_events) == 1
    details = transfer_events[0]["details"]
    assert details["old_owner_user_id"] == "usr_oldowner001"
    assert details["old_owner_dept_id"] is None
    assert details["new_owner_user_id"] == "usr_newowner01"
    assert details["reason"] == "moved to other team"


@pytest.mark.asyncio
async def test_transfer_integrity_error_translates_to_conflict(adb, monkeypatch) -> None:
    cred = await cred_repo.create(
        adb,
        id="cred_trfix02",
        name="trfix02",
        service="jira",
        scope="personal",
        owner_user_id="usr_oldowner002",
        owner_dept_id=None,
        owner_user_dept_id="dep_oldowner02",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_user_deleted",
        created_by="usr_oldowner002",
    )
    await adb.commit()

    async def _boom_commit() -> None:
        raise IntegrityError("statement", {}, Exception("partial UNIQUE"))

    monkeypatch.setattr(adb, "commit", _boom_commit)

    with patch.object(credential_service.audit_service, "emit", lambda *a, **k: None):
        with pytest.raises(ConflictError) as exc:
            await credential_service.transfer(
                adb,
                _admin(),
                cred.id,
                TransferRequest(new_owner_user_id="usr_newowner02", reason="race test"),
            )

    assert exc.value.error_code == "NAME_DUPLICATE"
    assert exc.value.http_status == 409


@pytest.mark.asyncio
async def test_recover_integrity_error_translates_to_conflict(adb, monkeypatch) -> None:
    cred = await cred_repo.create(
        adb,
        id="cred_rcfix01",
        name="rcfix01",
        service="jira",
        scope="personal",
        owner_user_id="usr_oldowner003",
        owner_dept_id=None,
        owner_user_dept_id="dep_oldowner03",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_user_deleted",
        created_by="usr_oldowner003",
    )
    from datetime import datetime, timezone
    cred.blocked_at = datetime.now(timezone.utc)
    await adb.commit()

    async def _boom_commit() -> None:
        raise IntegrityError("statement", {}, Exception("partial UNIQUE"))

    monkeypatch.setattr(adb, "commit", _boom_commit)

    with patch.object(credential_service.audit_service, "emit", lambda *a, **k: None):
        with pytest.raises(ConflictError) as exc:
            await credential_service.recover(adb, _admin(), cred.id)

    assert exc.value.error_code == "NAME_DUPLICATE"
