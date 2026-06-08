"""`sweep_service.sweep_expired_blocked` — retention-based hard delete."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.repositories import credentials as cred_repo
from src.services import sweep_service


ACTOR = "usr_admin000000000000000000000a1"
USER = "usr_alice0000000000000000000000a1"


async def _blocked_cred(adb, *, cred_id: str, blocked_at: datetime, name: str = "n"):
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name=name,
        service="jira",
        scope="personal",
        owner_user_id=USER,
        owner_dept_id=None,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by=ACTOR,
    )
    await adb.commit()
    cred.status = "blocked"
    cred.blocked_at = blocked_at
    cred.blocked_reason = "owner_user_deleted"
    await adb.commit()
    return cred


@pytest.mark.asyncio
async def test_sweep_skips_recent_blocked(adb):
    """blocked_at < 30 дней назад — не трогаем."""
    fresh = await _blocked_cred(
        adb, cred_id="cred_sw_a01", name="fresh",
        blocked_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    with patch.object(sweep_service.audit_service, "emit", side_effect=lambda *a, **k: None):
        summary = await sweep_service.sweep_expired_blocked(adb)

    assert summary["deleted_count"] == 0
    assert summary["errors"] == []
    assert await cred_repo.get_by_id(adb, fresh.id) is not None


@pytest.mark.asyncio
async def test_sweep_deletes_expired(adb):
    """blocked_at > 30 дней назад — удаляем."""
    old = await _blocked_cred(
        adb, cred_id="cred_sw_b01", name="old",
        blocked_at=datetime.now(timezone.utc) - timedelta(days=45),
    )

    emitted = []
    with patch.object(
        sweep_service.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    ):
        summary = await sweep_service.sweep_expired_blocked(adb)

    assert summary["deleted_count"] == 1
    assert summary["errors"] == []
    assert await cred_repo.get_by_id(adb, old.id) is None
    deletes = [(a, kw) for a, kw in emitted if a == "tokens.delete"]
    assert len(deletes) == 1
    assert deletes[0][1]["details"]["auto_delete"] is True
    assert deletes[0][1]["details"]["reason"] == "blocked_window_expired"


@pytest.mark.asyncio
async def test_sweep_handles_multiple_rows(adb):
    """Несколько строк подряд: только expired улетают."""
    fresh = await _blocked_cred(
        adb, cred_id="cred_sw_c01", name="fresh1",
        blocked_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    expired1 = await _blocked_cred(
        adb, cred_id="cred_sw_c02", name="expired1",
        blocked_at=datetime.now(timezone.utc) - timedelta(days=31),
    )
    expired2 = await _blocked_cred(
        adb, cred_id="cred_sw_c03", name="expired2",
        blocked_at=datetime.now(timezone.utc) - timedelta(days=100),
    )

    with patch.object(sweep_service.audit_service, "emit", side_effect=lambda *a, **k: None):
        summary = await sweep_service.sweep_expired_blocked(adb)

    assert summary["deleted_count"] == 2
    assert await cred_repo.get_by_id(adb, fresh.id) is not None
    assert await cred_repo.get_by_id(adb, expired1.id) is None
    assert await cred_repo.get_by_id(adb, expired2.id) is None


@pytest.mark.asyncio
async def test_sweep_honours_settings_retention(adb):
    """`blocked_retention_days=10` режет более молодую границу."""
    cred = await _blocked_cred(
        adb, cred_id="cred_sw_d01", name="d",
        blocked_at=datetime.now(timezone.utc) - timedelta(days=12),
    )

    class _Stub:
        blocked_retention_days = 10

    with patch.object(sweep_service, "get_settings", lambda: _Stub()), \
         patch.object(sweep_service.audit_service, "emit", side_effect=lambda *a, **k: None):
        summary = await sweep_service.sweep_expired_blocked(adb)

    assert summary["deleted_count"] == 1
    assert await cred_repo.get_by_id(adb, cred.id) is None


@pytest.mark.asyncio
async def test_sweep_collects_handler_errors(adb):
    """Если SQL-DELETE падает — sweep ловит, summary.errors заполнен.

    Sweep делает атомарный `DELETE ... RETURNING` за один execute; чтобы
    смоделировать сбой, патчим `adb.execute` и поднимаем оттуда.
    """
    await _blocked_cred(
        adb, cred_id="cred_sw_e01", name="e",
        blocked_at=datetime.now(timezone.utc) - timedelta(days=99),
    )

    async def _boom(*_a, **_k):
        raise RuntimeError("simulated delete failure")

    with patch.object(adb, "execute", side_effect=_boom), \
         patch.object(sweep_service.audit_service, "emit", side_effect=lambda *a, **k: None):
        summary = await sweep_service.sweep_expired_blocked(adb)

    assert summary["deleted_count"] == 0
    assert summary["errors"]
    assert "simulated delete failure" in summary["errors"][0]
