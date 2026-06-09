"""Матрица проверок access_service.check_access.

Покрытие:
* personal — owner / чужой через ACL / без ACL;
* department — внутри owner_dep / снаружи;
* cross_department — owner_dep / recipient_dep с DeptGrant / без него;
* admin overrides — admin secret_service'а своего dept'а read-only /
  cross-dept admin отбит; account_admin к содержимому секретов не
  допускается (нет read/recover/transfer);
* blocked cred — block all except admin override своего dept'а;
* can_read vs can_write на разные action'ы.
"""

from __future__ import annotations

import pytest

from src.dependencies.auth import Identity
from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.repositories import role_acls as acls_repo
from src.services import access_service


def _envelope() -> str:
    return "v2$nonce$ct"


def _identity(
    *,
    user_id: str = "usr_actor000000000000000000001",
    actor_type: str = "user",
    department_id: str | None = "dep_actor00000000000000000001",
    roles: list[str] | None = None,
    platform_role: str | None = None,
) -> Identity:
    return Identity(
        user_id=user_id,
        username="actor",
        actor_type=actor_type,
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles or []},
        is_banned=False,
        platform_role=platform_role,
    )


async def _create_personal(
    adb,
    *,
    id: str = "cred_pers_a01",
    owner_user_id: str = "usr_actor000000000000000000001",
    owner_user_dept_id: str | None = None,
    status: str = "active",
):
    return await cred_repo.create(
        adb,
        id=id,
        name=f"name_{id}",
        service="jira",
        scope="personal",
        owner_user_id=owner_user_id,
        owner_dept_id=None,
        owner_user_dept_id=owner_user_dept_id,
        login="x",
        secret_encrypted=_envelope(),
        status=status,
        created_by="usr_admin0000000000000000000001",
    )


async def _create_dept(
    adb,
    *,
    id: str = "cred_dept_a01",
    owner_dept_id: str = "dep_actor00000000000000000001",
    scope: str = "department",
    status: str = "active",
):
    return await cred_repo.create(
        adb,
        id=id,
        name=f"name_{id}",
        service="jira",
        scope=scope,
        owner_user_id=None,
        owner_dept_id=owner_dept_id,
        login="x",
        secret_encrypted=_envelope(),
        status=status,
        created_by="usr_admin0000000000000000000001",
    )


# ── personal ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_personal_owner_can_do_everything(adb) -> None:
    cred = await _create_personal(adb)
    actor = _identity()
    for action in ["read", "reveal", "write", "delete", "grant_acl", "manage_status"]:
        allowed, reason = await access_service.check_access(adb, actor, cred, action)
        assert allowed, f"{action} denied: {reason}"
        assert reason == "owner_match"


@pytest.mark.asyncio
async def test_personal_non_owner_without_acl_denied(adb) -> None:
    cred = await _create_personal(adb)
    actor = _identity(user_id="usr_other000000000000000000001", roles=["reader"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "read")
    assert not allowed
    assert reason == "role_not_in_acl"


@pytest.mark.asyncio
async def test_personal_grantee_with_can_read_allowed(adb) -> None:
    cred = await _create_personal(adb)
    await acls_repo.create(
        adb,
        id="acl_p1",
        cred_id=cred.id,
        dept_id="dep_actor00000000000000000001",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    actor = _identity(
        user_id="usr_other000000000000000000001",
        roles=["reader"],
    )
    allowed, reason = await access_service.check_access(adb, actor, cred, "reveal")
    assert allowed
    assert reason == "acl_read"


@pytest.mark.asyncio
async def test_personal_grantee_without_can_read_denied(adb) -> None:
    cred = await _create_personal(adb)
    await acls_repo.create(
        adb,
        id="acl_p2",
        cred_id=cred.id,
        dept_id="dep_actor00000000000000000001",
        role_name="reader",
        can_read=False,
        can_write=False,
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    actor = _identity(
        user_id="usr_other000000000000000000001",
        roles=["reader"],
    )
    allowed, reason = await access_service.check_access(adb, actor, cred, "reveal")
    assert not allowed
    assert reason == "acl_missing_can_read"


@pytest.mark.asyncio
async def test_personal_non_owner_cannot_grant_acl(adb) -> None:
    cred = await _create_personal(adb)
    actor = _identity(user_id="usr_other000000000000000000001", roles=["reader"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "grant_acl")
    assert not allowed
    assert reason == "scope_mismatch"


@pytest.mark.asyncio
async def test_personal_other_dep_user_denied(adb) -> None:
    cred = await _create_personal(adb)
    actor = _identity(
        user_id="usr_other000000000000000000001",
        department_id="dep_other00000000000000000001",
        roles=["reader"],
    )
    allowed, _ = await access_service.check_access(adb, actor, cred, "read")
    assert not allowed


# ── department ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dept_admin_in_owner_dep_allowed_all(adb) -> None:
    cred = await _create_dept(adb)
    actor = _identity(platform_role="department_admin")
    for action in ["read", "reveal", "write", "delete", "grant_acl", "grant_dept", "manage_status"]:
        allowed, reason = await access_service.check_access(adb, actor, cred, action)
        assert allowed, f"{action} denied: {reason}"
        assert reason == "dept_admin"


@pytest.mark.asyncio
async def test_dept_reader_in_owner_dep_via_acl(adb) -> None:
    cred = await _create_dept(adb)
    await acls_repo.create(
        adb,
        id="acl_d1",
        cred_id=cred.id,
        dept_id="dep_actor00000000000000000001",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    actor = _identity(roles=["reader"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "reveal")
    assert allowed and reason == "acl_read"


@pytest.mark.asyncio
async def test_dept_reader_cannot_write(adb) -> None:
    cred = await _create_dept(adb)
    await acls_repo.create(
        adb,
        id="acl_d2",
        cred_id=cred.id,
        dept_id="dep_actor00000000000000000001",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    actor = _identity(roles=["reader"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "write")
    assert not allowed
    assert reason == "acl_missing_can_write"


@pytest.mark.asyncio
async def test_dept_other_dep_actor_denied(adb) -> None:
    cred = await _create_dept(adb, owner_dept_id="dep_owner000000000000000001")
    actor = _identity(roles=["reader"])  # его dept = dep_actor...
    allowed, reason = await access_service.check_access(adb, actor, cred, "read")
    assert not allowed
    assert reason == "scope_mismatch"


# ── cross_department ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_dep_owner_dep_admin_allowed(adb) -> None:
    cred = await _create_dept(adb, scope="cross_department")
    actor = _identity(platform_role="department_admin")
    allowed, reason = await access_service.check_access(adb, actor, cred, "grant_dept")
    assert allowed and reason == "dept_admin"


@pytest.mark.asyncio
async def test_cross_dep_recipient_without_grant_denied(adb) -> None:
    cred = await _create_dept(adb, scope="cross_department", owner_dept_id="dep_owner000000000000000001")
    actor = _identity(roles=["reader"])  # его dept = dep_actor, не owner
    allowed, reason = await access_service.check_access(adb, actor, cred, "read")
    assert not allowed
    assert reason == "dept_grant_missing"


@pytest.mark.asyncio
async def test_cross_dep_recipient_with_grant_but_no_acl(adb) -> None:
    cred = await _create_dept(adb, scope="cross_department", owner_dept_id="dep_owner000000000000000001")
    await grants_repo.create(
        adb,
        id="dgr_acc1",
        cred_id=cred.id,
        recipient_dept_id="dep_actor00000000000000000001",
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    await adb.commit()
    actor = _identity(roles=["reader"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "read")
    assert not allowed
    assert reason == "role_not_in_acl"


@pytest.mark.asyncio
async def test_cross_dep_recipient_with_grant_and_acl(adb) -> None:
    cred = await _create_dept(adb, scope="cross_department", owner_dept_id="dep_owner000000000000000001")
    await grants_repo.create(
        adb,
        id="dgr_acc2",
        cred_id=cred.id,
        recipient_dept_id="dep_actor00000000000000000001",
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    await acls_repo.create(
        adb,
        id="acl_acc2",
        cred_id=cred.id,
        dept_id="dep_actor00000000000000000001",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    await adb.commit()
    actor = _identity(roles=["reader"])
    allowed, reason = await access_service.check_access(adb, actor, cred, "reveal")
    assert allowed and reason == "acl_read"


@pytest.mark.asyncio
async def test_cross_dep_recipient_cannot_delete(adb) -> None:
    cred = await _create_dept(adb, scope="cross_department", owner_dept_id="dep_owner000000000000000001")
    await grants_repo.create(
        adb,
        id="dgr_acc3",
        cred_id=cred.id,
        recipient_dept_id="dep_actor00000000000000000001",
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    await adb.commit()
    actor = _identity(platform_role="department_admin")
    allowed, reason = await access_service.check_access(adb, actor, cred, "delete")
    assert not allowed
    assert reason == "not_owner_dept"


@pytest.mark.asyncio
async def test_cross_dep_recipient_dep_admin_can_grant_acl(adb) -> None:
    cred = await _create_dept(adb, scope="cross_department", owner_dept_id="dep_owner000000000000000001")
    await grants_repo.create(
        adb,
        id="dgr_acc4",
        cred_id=cred.id,
        recipient_dept_id="dep_actor00000000000000000001",
        granted_by_user_id="usr_admin0000000000000000000001",
    )
    await adb.commit()
    actor = _identity(platform_role="department_admin")
    allowed, reason = await access_service.check_access(adb, actor, cred, "grant_acl")
    assert allowed and reason == "dept_admin"


# ── blocked ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocked_blocks_owner(adb) -> None:
    cred = await _create_personal(adb, status="blocked")
    actor = _identity()  # owner
    allowed, reason = await access_service.check_access(adb, actor, cred, "reveal")
    assert not allowed and reason == "blocked"


@pytest.mark.asyncio
async def test_blocked_service_admin_can_read(adb) -> None:
    cred = await _create_personal(adb, status="blocked")
    admin = _identity(
        user_id="usr_svc_admin000000000000000001",
        roles=["admin"],
    )
    allowed, reason = await access_service.check_access(adb, admin, cred, "read")
    assert allowed and reason == "admin_override"


@pytest.mark.asyncio
async def test_blocked_service_admin_can_recover(adb) -> None:
    cred = await _create_personal(adb, status="blocked")
    admin = _identity(
        user_id="usr_svc_admin000000000000000002",
        roles=["admin"],
    )
    allowed, reason = await access_service.check_access(adb, admin, cred, "manage_status")
    assert allowed and reason == "service_admin"


@pytest.mark.asyncio
async def test_blocked_account_admin_cannot_recover(adb) -> None:
    """account_admin к recover не пускается: платформенный админ не имеет
    доступа к содержимому секретов. Recover'ить blocked-cred может только
    admin secret_service'а владеющего dept'а."""
    cred = await _create_personal(adb, status="blocked")
    aa = _identity(
        user_id="usr_acc_admin0000000000000000001",
        platform_role="account_admin",
    )
    allowed, reason = await access_service.check_access(adb, aa, cred, "manage_status")
    assert not allowed
    assert reason == "blocked"


# ── admin overrides on active creds ───────────────────────────────────────


@pytest.mark.asyncio
async def test_service_admin_read_any_personal(adb) -> None:
    cred = await _create_personal(adb, owner_user_id="usr_someone0000000000000000001")
    admin = _identity(roles=["admin"])
    allowed, reason = await access_service.check_access(adb, admin, cred, "read")
    assert allowed and reason == "admin_override"


@pytest.mark.asyncio
async def test_service_admin_cannot_reveal(adb) -> None:
    """Service admin может read но НЕ reveal — это сильнее, чем metadata."""
    cred = await _create_personal(adb, owner_user_id="usr_someone0000000000000000001")
    admin = _identity(roles=["admin"])
    allowed, reason = await access_service.check_access(adb, admin, cred, "reveal")
    assert not allowed
    # Реальная причина — он не owner и не в его dep'е, попадает в scope_mismatch.
    assert reason in {"scope_mismatch", "role_not_in_acl"}


@pytest.mark.asyncio
async def test_service_admin_in_other_dept_cannot_read_personal(adb) -> None:
    """admin secret_service'а dep_B НЕ имеет read-override на personal-cred'у
    юзера из dep_A. Cross-dept привилегий у per-(dept, service) роли нет.
    """
    cred = await _create_personal(
        adb,
        id="cred_pers_dept_a",
        owner_user_id="usr_in_dept_a000000000000000001",
        owner_user_dept_id="dep_owner00000000000000000001",
    )
    foreign_admin = _identity(
        user_id="usr_foreign_admin0000000000000001",
        department_id="dep_foreign0000000000000000001",
        roles=["admin"],
    )
    allowed, reason = await access_service.check_access(adb, foreign_admin, cred, "read")
    assert not allowed
    assert reason in {"scope_mismatch", "role_not_in_acl"}


@pytest.mark.asyncio
async def test_service_admin_in_other_dept_cannot_recover_blocked(adb) -> None:
    """Blocked-cred dep_A: admin dep_B не должен мочь recover'нуть её."""
    cred = await _create_personal(
        adb,
        id="cred_pers_blkB",
        owner_user_id="usr_blocked_pers_owner00000001",
        owner_user_dept_id="dep_owner00000000000000000001",
        status="blocked",
    )
    foreign_admin = _identity(
        user_id="usr_foreign_admin_rec00000000001",
        department_id="dep_foreign0000000000000000001",
        roles=["admin"],
    )
    allowed, reason = await access_service.check_access(
        adb, foreign_admin, cred, "manage_status"
    )
    assert not allowed and reason == "blocked"


@pytest.mark.asyncio
async def test_service_admin_in_other_dept_cannot_read_blocked(adb) -> None:
    """Blocked-cred dep_A — admin dep_B не должен видеть её для аудита."""
    cred = await _create_personal(
        adb,
        id="cred_pers_blkR",
        owner_user_id="usr_blocked_pers_owner_r0000001",
        owner_user_dept_id="dep_owner00000000000000000001",
        status="blocked",
    )
    foreign_admin = _identity(
        user_id="usr_foreign_admin_blkR0000000001",
        department_id="dep_foreign0000000000000000001",
        roles=["admin"],
    )
    allowed, reason = await access_service.check_access(adb, foreign_admin, cred, "read")
    assert not allowed and reason == "blocked"


@pytest.mark.asyncio
async def test_account_admin_cannot_write_cross_dep(adb) -> None:
    """account_admin не имеет доступа к содержимому секретов: cross_dep
    transfer/write возможен только для admin secret_service'а владеющего
    dept'а; если owner_dep удалён, восстановление идёт через lifecycle."""
    cred = await _create_dept(adb, scope="cross_department", owner_dept_id="dep_dead0000000000000000000001")
    aa = _identity(
        user_id="usr_acc_admin0000000000000000003",
        department_id=None,
        platform_role="account_admin",
    )
    allowed, reason = await access_service.check_access(adb, aa, cred, "write")
    assert not allowed
    assert reason == "scope_mismatch"


@pytest.mark.asyncio
async def test_account_admin_cannot_write_department_scope(adb) -> None:
    cred = await _create_dept(adb, scope="department", owner_dept_id="dep_dead0000000000000000000001")
    aa = _identity(
        user_id="usr_acc_admin0000000000000000004",
        department_id=None,
        platform_role="account_admin",
    )
    allowed, reason = await access_service.check_access(adb, aa, cred, "write")
    assert not allowed
    assert reason == "scope_mismatch"


# ── bot identity на personal ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bot_cannot_see_personal(adb) -> None:
    cred = await _create_personal(adb)
    bot = _identity(actor_type="bot", user_id="bot_xxx000000000000000000001", roles=["reader"])
    allowed, reason = await access_service.check_access(adb, bot, cred, "read")
    assert not allowed
    assert reason == "scope_mismatch"
