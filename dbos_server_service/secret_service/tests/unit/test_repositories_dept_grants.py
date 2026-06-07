"""Тесты CRUD-репозитория DeptGrant."""

from __future__ import annotations

import pytest

from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as repo


def _envelope() -> str:
    return "v2$nonce$ct"


async def _make_cred(adb, *, id: str = "cred_grant_base01") -> str:
    await cred_repo.create(
        adb,
        id=id,
        name=f"name_{id}",
        service="jira",
        scope="cross_department",
        owner_user_id=None,
        owner_dept_id="dep_owner000000000000000001",
        login="bot",
        secret_encrypted=_envelope(),
        status="active",
        created_by="usr_admin0000000000000000000001",
    )
    return id


def _grant(
    *,
    id: str,
    cred_id: str,
    recipient_dept_id: str = "dep_recv00000000000000000001",
) -> dict:
    return dict(
        id=id,
        cred_id=cred_id,
        recipient_dept_id=recipient_dept_id,
        granted_by_user_id="usr_admin0000000000000000000001",
    )


# ── CRUD basics ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_exists(adb) -> None:
    cid = await _make_cred(adb)
    await repo.create(adb, **_grant(id="dgr_01", cred_id=cid))
    await adb.commit()
    assert await repo.exists_for(adb, cid, "dep_recv00000000000000000001") is True


@pytest.mark.asyncio
async def test_exists_for_negative(adb) -> None:
    cid = await _make_cred(adb)
    await adb.commit()
    assert await repo.exists_for(adb, cid, "dep_nope000000000000000000001") is False


@pytest.mark.asyncio
async def test_find_returns_object(adb) -> None:
    cid = await _make_cred(adb)
    await repo.create(adb, **_grant(id="dgr_02", cred_id=cid))
    await adb.commit()
    g = await repo.find(adb, cid, "dep_recv00000000000000000001")
    assert g is not None
    assert g.id == "dgr_02"


@pytest.mark.asyncio
async def test_get_for_cred(adb) -> None:
    cid = await _make_cred(adb)
    await repo.create(adb, **_grant(id="dgr_03", cred_id=cid, recipient_dept_id="dep_a01000000000000000000001"))
    await repo.create(adb, **_grant(id="dgr_04", cred_id=cid, recipient_dept_id="dep_b01000000000000000000001"))
    await adb.commit()
    rows = await repo.get_for_cred(adb, cid)
    assert {r.id for r in rows} == {"dgr_03", "dgr_04"}


@pytest.mark.asyncio
async def test_delete(adb) -> None:
    cid = await _make_cred(adb)
    grant = await repo.create(adb, **_grant(id="dgr_05", cred_id=cid))
    await repo.delete(adb, grant)
    await adb.commit()
    assert await repo.exists_for(adb, cid, "dep_recv00000000000000000001") is False


@pytest.mark.asyncio
async def test_delete_for_cred(adb) -> None:
    cid = await _make_cred(adb)
    await repo.create(adb, **_grant(id="dgr_06", cred_id=cid, recipient_dept_id="dep_a02000000000000000000001"))
    await repo.create(adb, **_grant(id="dgr_07", cred_id=cid, recipient_dept_id="dep_b02000000000000000000001"))
    await adb.commit()
    removed = await repo.delete_for_cred(adb, cid)
    await adb.commit()
    assert removed == 2
    assert await repo.get_for_cred(adb, cid) == []


@pytest.mark.asyncio
async def test_fk_cascade_on_cred_delete(adb) -> None:
    """При DELETE кред FK-CASCADE сносит и DeptGrant'ы."""
    cid = await _make_cred(adb, id="cred_grant_cascade01")
    await repo.create(adb, **_grant(id="dgr_08", cred_id=cid))
    await adb.commit()
    cred = await cred_repo.get_by_id(adb, cid)
    await cred_repo.delete(adb, cred)
    await adb.commit()
    assert await repo.find(adb, cid, "dep_recv00000000000000000001") is None
