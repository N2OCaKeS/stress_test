"""Прочие исправления: sweep atomic, reveal_throttle pipeline, guest 404, no autoflush mutation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.dependencies.auth import Identity
from src.repositories import credentials as cred_repo
from src.services import access_service, credential_service, reveal_throttle, sweep_service


@pytest.mark.asyncio
async def test_sweep_atomic_delete_uses_status_and_cutoff(adb) -> None:
    """Sweep удаляет только blocked+expired; active с прошлым blocked_at не должно даже существовать,
    но если кто-то задал — sweep пропускает."""
    # Создаём blocked + expired.
    cred = await cred_repo.create(
        adb,
        id="cred_swatom01",
        name="atom1",
        service="jira",
        scope="personal",
        owner_user_id="usr_swatom_o1",
        owner_dept_id=None,
        owner_user_dept_id="dep_swatom_o1",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_swatom_o1",
    )
    await adb.commit()
    cred.status = "blocked"
    cred.blocked_at = datetime.now(timezone.utc) - timedelta(days=45)
    cred.blocked_reason = "owner_user_deleted"
    await adb.commit()

    with patch.object(sweep_service.audit_service, "emit", lambda *a, **k: None):
        summary = await sweep_service.sweep_expired_blocked(adb)

    assert summary["deleted_count"] == 1
    assert await cred_repo.get_by_id(adb, cred.id) is None


@pytest.mark.asyncio
async def test_sweep_does_not_delete_recovered_recently(adb) -> None:
    """Гонка с recover: если параллельный recover уже поставил status=active,
    наш атомарный DELETE WHERE status='blocked' пропускает строку."""
    cred = await cred_repo.create(
        adb,
        id="cred_swatom02",
        name="atom2",
        service="jira",
        scope="personal",
        owner_user_id="usr_swatom_o2",
        owner_dept_id=None,
        owner_user_dept_id="dep_swatom_o2",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_swatom_o2",
    )
    # blocked_at старый, но статус active — sweep НЕ должен трогать.
    cred.blocked_at = datetime.now(timezone.utc) - timedelta(days=60)
    await adb.commit()

    with patch.object(sweep_service.audit_service, "emit", lambda *a, **k: None):
        summary = await sweep_service.sweep_expired_blocked(adb)

    assert summary["deleted_count"] == 0
    assert await cred_repo.get_by_id(adb, cred.id) is not None


@pytest.mark.asyncio
async def test_reveal_throttle_uses_pipeline() -> None:
    """`record_reveal` шлёт INCR+EXPIRE через `pipeline.execute()` атомарно."""
    reveal_throttle._reset_for_tests()
    fake_pipe = AsyncMock()
    fake_pipe.incr = lambda *a, **k: None
    fake_pipe.expire = lambda *a, **k: None
    fake_pipe.execute = AsyncMock(return_value=[1, True])

    class _Client:
        def pipeline(self):
            return fake_pipe

    with patch.object(reveal_throttle, "_ensure_redis_client", lambda: _Client()):
        is_first, count = await reveal_throttle.record_reveal("usr_x", "cred_x")

    assert is_first is True
    assert count == 1
    fake_pipe.execute.assert_awaited_once()
    reveal_throttle._reset_for_tests()


@pytest.mark.asyncio
async def test_guest_direct_get_returns_404_not_403(adb) -> None:
    """guest_role_no_access теперь в _NOT_VISIBLE_REASONS — прямой GET cred известного id отдаёт 404, не 403."""
    from src.core.exceptions import NotFoundError

    cred = await cred_repo.create(
        adb,
        id="cred_guest_enum01",
        name="ge01",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_guest_dep",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_admin_g01",
    )
    await adb.commit()

    guest = Identity(
        user_id="usr_guest01",
        username="guest",
        actor_type="user",
        department_id="dep_guest_dep",
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["guest"]},
        is_banned=False,
        platform_role=None,
    )
    with patch.object(credential_service.audit_service, "emit", lambda *a, **k: None):
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(adb, guest, cred.id, "read")


@pytest.mark.asyncio
async def test_would_have_read_access_does_not_mutate_cred(adb) -> None:
    """`_would_have_read_access_if_active` не меняет cred.status, использует status_override."""
    cred = await cred_repo.create(
        adb,
        id="cred_nomut01",
        name="nomut01",
        service="jira",
        scope="personal",
        owner_user_id="usr_nomut01",
        owner_dept_id=None,
        owner_user_dept_id="dep_nomut01",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="x",
        created_by="usr_nomut01",
    )
    await adb.commit()

    owner = Identity(
        user_id="usr_nomut01",
        username="o",
        actor_type="user",
        department_id="dep_nomut01",
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["reader"]},
        is_banned=False,
        platform_role=None,
    )
    saved_status = cred.status
    _ = await credential_service._would_have_read_access_if_active(adb, owner, cred)
    assert cred.status == saved_status == "blocked"


@pytest.mark.asyncio
async def test_check_access_status_override_active(adb) -> None:
    """`check_access(..., status_override='active')` обходит blocked-ветку без мутации."""
    cred = await cred_repo.create(
        adb,
        id="cred_so01",
        name="so01",
        service="jira",
        scope="personal",
        owner_user_id="usr_so01",
        owner_dept_id=None,
        owner_user_dept_id="dep_so01",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="x",
        created_by="usr_so01",
    )
    await adb.commit()
    owner = Identity(
        user_id="usr_so01",
        username="o",
        actor_type="user",
        department_id="dep_so01",
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["reader"]},
        is_banned=False,
        platform_role=None,
    )
    # Без override — blocked.
    allowed_b, reason_b = await access_service.check_access(adb, owner, cred, "read")
    assert not allowed_b and reason_b == "blocked"
    # С override — owner_match.
    allowed_a, reason_a = await access_service.check_access(
        adb, owner, cred, "read", status_override="active"
    )
    assert allowed_a and reason_a == "owner_match"
    # cred.status не изменился.
    assert cred.status == "blocked"
