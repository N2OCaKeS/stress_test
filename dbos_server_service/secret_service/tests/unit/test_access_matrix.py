"""Интеграция тип-wide матрицы прав в `access_service.check_access`.

Матрица — базовый allow-слой для неличных секретов отдела-владельца. Проверяем:
* роль с матричным грантом получает доступ к department/cross_department-кред'е
  (reason `matrix_role`), с соблюдением лесенки read ⊂ reveal ⊂ write;
* личные чужие секреты матрица НЕ открывает;
* per-credential RoleACL по-прежнему работает независимо (OR-семантика);
* на стороне получателя cross_department матрица не подменяет DeptGrant.
"""

from __future__ import annotations

import secrets as _secrets

import pytest

from src.dependencies.auth import Identity
from src.repositories import credentials as cred_repo
from src.repositories import entity_permissions as perm_repo
from src.repositories import role_acls as acls_repo
from src.services import access_service

ACTOR_DEPT = "dep_actor00000000000000000001"
OTHER_DEPT = "dep_owner000000000000000001"


def _identity(*, roles: list[str] | None = None, department_id: str = ACTOR_DEPT) -> Identity:
    return Identity(
        user_id="usr_actor000000000000000000001",
        username="actor",
        actor_type="user",
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles or []},
        is_banned=False,
        platform_role=None,
    )


async def _dept_cred(adb, *, id: str, scope: str = "department", owner_dept_id: str = ACTOR_DEPT):
    cred = await cred_repo.create(
        adb,
        id=id,
        name=f"n_{id}",
        service="jira",
        scope=scope,
        owner_user_id=None,
        owner_dept_id=owner_dept_id,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_admin0000000000000000000001",
    )
    await adb.commit()
    return cred


async def _grant_matrix(adb, *, role: str, action: str, department_id: str | None = ACTOR_DEPT):
    await perm_repo.grant(
        adb,
        permission_id=f"prm_{_secrets.token_hex(8)}",
        entity_type="secret",
        role=role,
        action=action,
        granted_by=None,
        department_id=department_id,
    )
    await adb.commit()


@pytest.mark.asyncio
async def test_matrix_reveal_grant_allows_reveal_and_read(adb) -> None:
    cred = await _dept_cred(adb, id="cred_mtx_rev1")
    await _grant_matrix(adb, role="reader", action="reveal")
    actor = _identity(roles=["reader"])

    allowed, reason = await access_service.check_access(adb, actor, cred, "reveal")
    assert allowed and reason == "matrix_role"

    allowed, reason = await access_service.check_access(adb, actor, cred, "read")
    assert allowed and reason == "matrix_role"

    # reveal-грант не даёт write.
    allowed, reason = await access_service.check_access(adb, actor, cred, "write")
    assert not allowed


@pytest.mark.asyncio
async def test_matrix_write_grant_implies_reveal_and_read(adb) -> None:
    cred = await _dept_cred(adb, id="cred_mtx_wr1")
    await _grant_matrix(adb, role="operator", action="write")
    actor = _identity(roles=["operator"])

    for action in ("read", "reveal", "write"):
        allowed, reason = await access_service.check_access(adb, actor, cred, action)
        assert allowed and reason == "matrix_role", f"{action}: {reason}"

    # write не влечёт delete — точечная привилегия.
    allowed, _ = await access_service.check_access(adb, actor, cred, "delete")
    assert not allowed


@pytest.mark.asyncio
async def test_matrix_read_only_no_reveal(adb) -> None:
    cred = await _dept_cred(adb, id="cred_mtx_rd1")
    await _grant_matrix(adb, role="reader", action="read")
    actor = _identity(roles=["reader"])

    allowed, reason = await access_service.check_access(adb, actor, cred, "read")
    assert allowed and reason == "matrix_role"

    allowed, _ = await access_service.check_access(adb, actor, cred, "reveal")
    assert not allowed


@pytest.mark.asyncio
async def test_matrix_delete_grant(adb) -> None:
    cred = await _dept_cred(adb, id="cred_mtx_del1")
    await _grant_matrix(adb, role="operator", action="delete")
    actor = _identity(roles=["operator"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "delete")
    assert allowed and reason == "matrix_role"


@pytest.mark.asyncio
async def test_matrix_does_not_open_others_personal(adb) -> None:
    """Матричный грант в отделе actor'а не открывает чужой личный секрет."""
    cred = await cred_repo.create(
        adb,
        id="cred_mtx_pers1",
        name="pers",
        service="jira",
        scope="personal",
        owner_user_id="usr_someone0000000000000000001",
        owner_dept_id=None,
        owner_user_dept_id=ACTOR_DEPT,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_someone0000000000000000001",
    )
    await adb.commit()
    await _grant_matrix(adb, role="reader", action="reveal")
    actor = _identity(roles=["reader"])
    allowed, _ = await access_service.check_access(adb, actor, cred, "reveal")
    assert not allowed


@pytest.mark.asyncio
async def test_matrix_does_not_leak_cross_dept_recipient(adb) -> None:
    """У получателя cross_department матрица своего отдела не подменяет DeptGrant."""
    cred = await _dept_cred(
        adb, id="cred_mtx_x1", scope="cross_department", owner_dept_id=OTHER_DEPT
    )
    await _grant_matrix(adb, role="reader", action="reveal", department_id=ACTOR_DEPT)
    actor = _identity(roles=["reader"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "reveal")
    assert not allowed
    assert reason == "dept_grant_missing"


@pytest.mark.asyncio
async def test_per_credential_acl_still_works_without_matrix(adb) -> None:
    """RoleACL остаётся самостоятельным источником allow (OR с матрицей)."""
    cred = await _dept_cred(adb, id="cred_mtx_acl1")
    await acls_repo.create(
        adb,
        id="acl_mtx1",
        cred_id=cred.id,
        dept_id=ACTOR_DEPT,
        role_name="reader",
        can_view=True,
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    await adb.commit()
    actor = _identity(roles=["reader"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "reveal")
    assert allowed and reason == "acl_read"
