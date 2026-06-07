"""Тесты CRUD-репозитория Credential."""

from __future__ import annotations

import pytest

from src.models import Credential
from src.repositories import credentials as repo


def _envelope() -> str:
    return "v2$nonce$ct"


def _personal(
    *,
    id: str,
    user_id: str = "usr_aaaaaaaaaaaaaaaaaaaa01",
    name: str = "jira_personal",
    service: str = "jira",
    status: str = "active",
) -> dict:
    return dict(
        id=id,
        name=name,
        service=service,
        scope="personal",
        owner_user_id=user_id,
        owner_dept_id=None,
        login="alice",
        secret_encrypted=_envelope(),
        status=status,
        created_by="usr_admin0000000000000000000001",
    )


def _dept(
    *,
    id: str,
    dept_id: str = "dep_bbbbbbbbbbbbbbbbbbbb01",
    scope: str = "department",
    name: str = "jira_bot",
    service: str = "jira",
    status: str = "active",
) -> dict:
    return dict(
        id=id,
        name=name,
        service=service,
        scope=scope,
        owner_user_id=None,
        owner_dept_id=dept_id,
        login="bot",
        secret_encrypted=_envelope(),
        status=status,
        created_by="usr_admin0000000000000000000001",
    )


# ── CRUD basics ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get(adb) -> None:
    cred = await repo.create(adb, **_personal(id="cred_a01"))
    await adb.commit()
    found = await repo.get_by_id(adb, "cred_a01")
    assert found is not None
    assert found.id == cred.id


@pytest.mark.asyncio
async def test_get_by_id_missing(adb) -> None:
    assert await repo.get_by_id(adb, "cred_nope") is None


@pytest.mark.asyncio
async def test_get_by_id_for_update(adb) -> None:
    await repo.create(adb, **_personal(id="cred_a02"))
    await adb.commit()
    found = await repo.get_by_id_for_update(adb, "cred_a02")
    assert found is not None and found.id == "cred_a02"


@pytest.mark.asyncio
async def test_update(adb) -> None:
    cred = await repo.create(adb, **_personal(id="cred_a03"))
    await repo.update(adb, cred, name="jira_renamed", login="bob")
    await adb.commit()
    refreshed = await repo.get_by_id(adb, "cred_a03")
    assert refreshed.name == "jira_renamed"
    assert refreshed.login == "bob"


@pytest.mark.asyncio
async def test_delete(adb) -> None:
    cred = await repo.create(adb, **_personal(id="cred_a04"))
    await repo.delete(adb, cred)
    await adb.commit()
    assert await repo.get_by_id(adb, "cred_a04") is None


# ── Status transitions ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_blocked(adb) -> None:
    cred = await repo.create(adb, **_personal(id="cred_a05"))
    await repo.mark_blocked(adb, cred, reason="owner_deleted")
    await adb.commit()
    refreshed = await repo.get_by_id(adb, "cred_a05")
    assert refreshed.status == "blocked"
    assert refreshed.blocked_at is not None
    assert refreshed.blocked_reason == "owner_deleted"


@pytest.mark.asyncio
async def test_mark_active_clears_block_metadata(adb) -> None:
    cred = await repo.create(adb, **_personal(id="cred_a06", status="active"))
    await repo.mark_blocked(adb, cred, reason="oops")
    await repo.mark_active(adb, cred)
    await adb.commit()
    refreshed = await repo.get_by_id(adb, "cred_a06")
    assert refreshed.status == "active"
    assert refreshed.blocked_at is None
    assert refreshed.blocked_reason is None


# ── Unique pre-check ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_active_by_owner_service_name_hit(adb) -> None:
    await repo.create(adb, **_personal(id="cred_a07", name="dup", service="jira"))
    await adb.commit()
    found = await repo.find_active_by_owner_service_name(
        adb,
        owner_user_id="usr_aaaaaaaaaaaaaaaaaaaa01",
        owner_dept_id=None,
        service="jira",
        name="dup",
    )
    assert found is not None
    assert found.id == "cred_a07"


@pytest.mark.asyncio
async def test_find_active_ignores_blocked(adb) -> None:
    cred = await repo.create(
        adb, **_personal(id="cred_a08", name="dup2", service="jira")
    )
    await repo.mark_blocked(adb, cred, reason="x")
    await adb.commit()
    found = await repo.find_active_by_owner_service_name(
        adb,
        owner_user_id="usr_aaaaaaaaaaaaaaaaaaaa01",
        owner_dept_id=None,
        service="jira",
        name="dup2",
    )
    assert found is None


@pytest.mark.asyncio
async def test_find_active_dept_owner(adb) -> None:
    await repo.create(adb, **_dept(id="cred_d01", name="bot1"))
    await adb.commit()
    found = await repo.find_active_by_owner_service_name(
        adb,
        owner_user_id=None,
        owner_dept_id="dep_bbbbbbbbbbbbbbbbbbbb01",
        service="jira",
        name="bot1",
    )
    assert found is not None
    assert found.id == "cred_d01"


# ── Listing & pagination ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_for_user_returns_only_user(adb) -> None:
    await repo.create(adb, **_personal(id="cred_u1", name="a"))
    await repo.create(adb, **_personal(id="cred_u2", name="b"))
    await repo.create(
        adb,
        **_personal(id="cred_u3", user_id="usr_other000000000000000001", name="c"),
    )
    await adb.commit()
    rows = await repo.list_for_user(
        adb, "usr_aaaaaaaaaaaaaaaaaaaa01", limit=10
    )
    assert {r.id for r in rows} == {"cred_u1", "cred_u2"}


@pytest.mark.asyncio
async def test_list_for_user_filter_by_status(adb) -> None:
    c1 = await repo.create(adb, **_personal(id="cred_u4", name="x"))
    await repo.create(adb, **_personal(id="cred_u5", name="y"))
    await repo.mark_blocked(adb, c1, reason="x")
    await adb.commit()
    active = await repo.list_for_user(
        adb, "usr_aaaaaaaaaaaaaaaaaaaa01", status="active", limit=10
    )
    blocked = await repo.list_for_user(
        adb, "usr_aaaaaaaaaaaaaaaaaaaa01", status="blocked", limit=10
    )
    assert {r.id for r in active} == {"cred_u5"}
    assert {r.id for r in blocked} == {"cred_u4"}


@pytest.mark.asyncio
async def test_list_for_user_cursor_pagination(adb) -> None:
    for i in range(5):
        await repo.create(adb, **_personal(id=f"cred_p{i:02d}", name=f"n{i}"))
    await adb.commit()
    page1 = await repo.list_for_user(
        adb, "usr_aaaaaaaaaaaaaaaaaaaa01", limit=2
    )
    assert len(page1) == 2
    last = page1[-1]
    page2 = await repo.list_for_user(
        adb,
        "usr_aaaaaaaaaaaaaaaaaaaa01",
        limit=2,
        cursor=(last.created_at, last.id),
    )
    assert len(page2) == 2
    # Не пересекаются
    assert set(r.id for r in page1).isdisjoint(set(r.id for r in page2))


@pytest.mark.asyncio
async def test_list_for_dept_returns_only_dept(adb) -> None:
    await repo.create(adb, **_dept(id="cred_d2", name="x"))
    await repo.create(adb, **_dept(id="cred_d3", name="y"))
    await repo.create(
        adb,
        **_dept(id="cred_d4", dept_id="dep_other00000000000000000001", name="z"),
    )
    await adb.commit()
    rows = await repo.list_for_dept(
        adb, "dep_bbbbbbbbbbbbbbbbbbbb01", limit=10
    )
    assert {r.id for r in rows} == {"cred_d2", "cred_d3"}


@pytest.mark.asyncio
async def test_list_for_dept_filter_by_scope(adb) -> None:
    await repo.create(adb, **_dept(id="cred_d5", scope="department", name="a"))
    await repo.create(adb, **_dept(id="cred_d6", scope="cross_department", name="b"))
    await adb.commit()
    cross = await repo.list_for_dept(
        adb, "dep_bbbbbbbbbbbbbbbbbbbb01", scope="cross_department", limit=10
    )
    assert {r.id for r in cross} == {"cred_d6"}


@pytest.mark.asyncio
async def test_create_dept_credential(adb) -> None:
    """sanity: dept-cred тоже создаётся через тот же create()."""
    cred = await repo.create(adb, **_dept(id="cred_d7"))
    await adb.commit()
    assert isinstance(cred, Credential)
    assert cred.owner_dept_id == "dep_bbbbbbbbbbbbbbbbbbbb01"
