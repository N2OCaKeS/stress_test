"""Тесты CRUD-репозитория RoleACL."""

from __future__ import annotations

import pytest

from src.repositories import credentials as cred_repo
from src.repositories import role_acls as repo


def _envelope() -> str:
    return "v2$nonce$ct"


async def _make_cred(adb, *, id: str = "cred_acl_base01") -> str:
    """Создать носитель-cred для ACL'ей (нужен FK)."""
    await cred_repo.create(
        adb,
        id=id,
        name=f"name_{id}",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_owner000000000000000001",
        login="bot",
        secret_encrypted=_envelope(),
        status="active",
        created_by="usr_admin0000000000000000000001",
    )
    return id


def _acl(
    *,
    id: str,
    cred_id: str,
    dept_id: str = "dep_owner000000000000000001",
    role_name: str = "reader",
    can_read: bool = True,
    can_write: bool = False,
) -> dict:
    return dict(
        id=id,
        cred_id=cred_id,
        dept_id=dept_id,
        role_name=role_name,
        can_read=can_read,
        can_write=can_write,
        granted_by_user_id="usr_admin0000000000000000000001",
    )


# ── CRUD basics ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_find(adb) -> None:
    cid = await _make_cred(adb)
    await repo.create(adb, **_acl(id="acl_01", cred_id=cid))
    await adb.commit()
    found = await repo.find(adb, cid, "dep_owner000000000000000001", "reader")
    assert found is not None
    assert found.id == "acl_01"


@pytest.mark.asyncio
async def test_find_missing(adb) -> None:
    cid = await _make_cred(adb)
    await adb.commit()
    assert await repo.find(adb, cid, "dep_x000000000000000000000001", "reader") is None


@pytest.mark.asyncio
async def test_get_for_cred_returns_all(adb) -> None:
    cid = await _make_cred(adb)
    await repo.create(adb, **_acl(id="acl_02", cred_id=cid, role_name="reader"))
    await repo.create(
        adb, **_acl(id="acl_03", cred_id=cid, role_name="operator", can_write=True)
    )
    await adb.commit()
    rows = await repo.get_for_cred(adb, cid)
    assert {r.id for r in rows} == {"acl_02", "acl_03"}


@pytest.mark.asyncio
async def test_get_for_cred_dept_scoped(adb) -> None:
    cid = await _make_cred(adb)
    await repo.create(adb, **_acl(id="acl_04", cred_id=cid, dept_id="dep_a01000000000000000000001"))
    await repo.create(adb, **_acl(id="acl_05", cred_id=cid, dept_id="dep_b01000000000000000000001"))
    await adb.commit()
    rows = await repo.get_for_cred_dept(adb, cid, "dep_a01000000000000000000001")
    assert {r.id for r in rows} == {"acl_04"}


@pytest.mark.asyncio
async def test_delete(adb) -> None:
    cid = await _make_cred(adb)
    acl = await repo.create(adb, **_acl(id="acl_06", cred_id=cid))
    await repo.delete(adb, acl)
    await adb.commit()
    assert await repo.find(adb, cid, "dep_owner000000000000000001", "reader") is None


@pytest.mark.asyncio
async def test_delete_for_cred_dept_cascade(adb) -> None:
    cid = await _make_cred(adb)
    await repo.create(adb, **_acl(id="acl_07", cred_id=cid, dept_id="dep_recv00000000000000000001", role_name="reader"))
    await repo.create(adb, **_acl(id="acl_08", cred_id=cid, dept_id="dep_recv00000000000000000001", role_name="operator"))
    await repo.create(adb, **_acl(id="acl_09", cred_id=cid, dept_id="dep_other0000000000000000001", role_name="reader"))
    await adb.commit()
    removed = await repo.delete_for_cred_dept(adb, cid, "dep_recv00000000000000000001")
    await adb.commit()
    assert removed == 2
    rest = await repo.get_for_cred(adb, cid)
    assert {r.id for r in rest} == {"acl_09"}


@pytest.mark.asyncio
async def test_delete_for_cred_dept_noop_when_empty(adb) -> None:
    cid = await _make_cred(adb)
    await adb.commit()
    removed = await repo.delete_for_cred_dept(adb, cid, "dep_nope000000000000000000001")
    assert removed == 0
