"""`credential_service.load_for_action` — audit-emission на denied-ветках.

Fix-агент 2026-06-07 отметил: 404 info-leak path молчит, SOC не видит попыток
несанкционированного доступа. Эти тесты фиксируют, что
`tokens.access_denied / failure` эмитится на каждой denied-ветке:

* personal cred, не-owner → 404 + emit с `reason=info_leak_404`;
* blocked cred, не-owner-без-доступа → 404 + emit с `reason=blocked` и
  `masked_as=404`;
* blocked cred, owner-с-read-доступом → 410 + emit с `reason=blocked` и
  `masked_as=410`;
* cross_department без DeptGrant → 404 + emit с `reason=info_leak_404` и
  `underlying_reason=dept_grant_missing`;
* реальный 403 (recipient-dep с grant'ом, но без can_read) → emit с
  конкретным reason от check_access;
* успешный доступ → emit НЕ происходит.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.exceptions import (
    AuthorizationError,
    GoneError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.repositories import role_acls as acls_repo
from src.services import credential_service


OWNER_ID = "usr_owner000000000000000000001"
OWNER_DEP = "dep_owner00000000000000000001"
OTHER_USER = "usr_other000000000000000000001"
OTHER_DEP = "dep_other00000000000000000001"
ADMIN_ACTOR = "usr_admin000000000000000000001"


def _identity(
    *,
    user_id: str = OTHER_USER,
    actor_type: str = "user",
    department_id: str | None = OTHER_DEP,
    roles: list[str] | None = None,
    platform_role: str | None = None,
) -> Identity:
    return Identity(
        user_id=user_id,
        username="actor",
        actor_type=actor_type,
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles or ["reader"]},
        is_banned=False,
        platform_role=platform_role,
    )


async def _make_personal(adb, *, cred_id: str, status: str = "active"):
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name=f"name_{cred_id}",
        service="jira",
        scope="personal",
        owner_user_id=OWNER_ID,
        owner_dept_id=None,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status=status,
        created_by=OWNER_ID,
    )
    await adb.commit()
    return cred


async def _make_cross_dep(adb, *, cred_id: str, status: str = "active"):
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name=f"name_{cred_id}",
        service="jira",
        scope="cross_department",
        owner_user_id=None,
        owner_dept_id=OWNER_DEP,
        login="x",
        secret_encrypted="v2$nonce$ct",
        status=status,
        created_by=ADMIN_ACTOR,
    )
    await adb.commit()
    return cred


def _collect_emits():
    """Контекст-менеджер, возвращающий список (action, kwargs) вызовов emit."""
    emitted: list[tuple[str, dict]] = []
    p = patch.object(
        credential_service.audit_service,
        "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    )
    return p, emitted


def _denied_calls(emitted: list[tuple[str, dict]]) -> list[dict]:
    return [kw for a, kw in emitted if a == "tokens.access_denied"]


# ── 404 info-leak (personal scope, non-owner) ─────────────────────────────


@pytest.mark.asyncio
async def test_personal_non_owner_404_emits_info_leak(adb) -> None:
    cred = await _make_personal(adb, cred_id="cred_il_p01")
    actor = _identity()

    patcher, emitted = _collect_emits()
    with patcher:
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(adb, actor, cred.id, "read")

    denied = _denied_calls(emitted)
    assert len(denied) == 1, f"expected exactly 1 access_denied, got {emitted}"
    kw = denied[0]
    assert kw["target_id"] == cred.id
    assert kw["target_type"] == "credential"
    assert kw["status"] == "failure"
    assert kw["allowed"] is False
    details = kw["details"]
    assert details["reason"] == "info_leak_404"
    assert details["scope"] == "personal"
    assert details["action"] == "read"
    # Underlying reason для personal не-owner без ACL — role_not_in_acl.
    assert details["underlying_reason"] == "role_not_in_acl"


@pytest.mark.asyncio
async def test_cross_dep_no_grant_404_emits_info_leak(adb) -> None:
    """cross_department + recipient_dep без DeptGrant → 404 info-leak."""
    cred = await _make_cross_dep(adb, cred_id="cred_il_x01")
    # actor — в OTHER_DEP, грантов нет, читать пытается.
    actor = _identity(department_id=OTHER_DEP, roles=["reader"])

    patcher, emitted = _collect_emits()
    with patcher:
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(adb, actor, cred.id, "read")

    denied = _denied_calls(emitted)
    assert len(denied) == 1
    details = denied[0]["details"]
    assert details["reason"] == "info_leak_404"
    assert details["scope"] == "cross_department"
    assert details["underlying_reason"] == "dept_grant_missing"


# ── 410 blocked (с/без read-доступа на active) ────────────────────────────


@pytest.mark.asyncio
async def test_blocked_owner_with_read_emits_masked_410(adb) -> None:
    """blocked personal cred + owner-actor → 410 + emit reason=blocked, masked_as=410."""
    cred = await _make_personal(adb, cred_id="cred_bl_p01", status="blocked")
    owner = _identity(user_id=OWNER_ID, department_id=OWNER_DEP, roles=["reader"])

    patcher, emitted = _collect_emits()
    with patcher:
        with pytest.raises(GoneError):
            await credential_service.load_for_action(adb, owner, cred.id, "read")

    denied = _denied_calls(emitted)
    assert len(denied) == 1
    details = denied[0]["details"]
    assert details["reason"] == "blocked"
    assert details["masked_as"] == "410"
    assert details["scope"] == "personal"


@pytest.mark.asyncio
async def test_blocked_non_owner_emits_masked_404(adb) -> None:
    """blocked personal cred + не-owner → 404 + emit reason=blocked, masked_as=404."""
    cred = await _make_personal(adb, cred_id="cred_bl_p02", status="blocked")
    actor = _identity()  # OTHER_USER в OTHER_DEP, не имеет доступа

    patcher, emitted = _collect_emits()
    with patcher:
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(adb, actor, cred.id, "read")

    denied = _denied_calls(emitted)
    assert len(denied) == 1
    details = denied[0]["details"]
    assert details["reason"] == "blocked"
    assert details["masked_as"] == "404"


# ── 403 — реальный access_denied (recipient видит cred, но прав нет) ──────


@pytest.mark.asyncio
async def test_recipient_with_grant_without_can_read_emits_real_403(adb) -> None:
    """cross_dep recipient + DeptGrant + ACL без флагов → 403 + emit с
    конкретным reason от check_access (acl_missing_can_view).

    ACL-строка с пустыми флагами (ни view, ни read, ни write) не даёт даже
    видимости метаданных, поэтому `read` отбивается на младшем уровне лесенки.
    """
    cred = await _make_cross_dep(adb, cred_id="cred_ad_x01")
    await grants_repo.create(
        adb,
        id="dg_ad_x01",
        cred_id=cred.id,
        recipient_dept_id=OTHER_DEP,
        granted_by_user_id=ADMIN_ACTOR,
    )
    await acls_repo.create(
        adb,
        id="acl_ad_x01",
        cred_id=cred.id,
        dept_id=OTHER_DEP,
        role_name="reader",
        can_read=False,
        can_write=False,
        granted_by_user_id=ADMIN_ACTOR,
    )
    await adb.commit()

    actor = _identity(department_id=OTHER_DEP, roles=["reader"])

    patcher, emitted = _collect_emits()
    with patcher:
        with pytest.raises(AuthorizationError):
            await credential_service.load_for_action(adb, actor, cred.id, "read")

    denied = _denied_calls(emitted)
    assert len(denied) == 1
    details = denied[0]["details"]
    # Реальный 403 — не маскировка, поэтому ни info_leak_404, ни blocked.
    assert details["reason"] not in {"info_leak_404", "blocked"}
    assert details["reason"] == "acl_missing_can_view"
    assert details["scope"] == "cross_department"


# ── Positive: valid access НЕ должен генерить access_denied ───────────────


@pytest.mark.asyncio
async def test_valid_access_does_not_emit_access_denied(adb) -> None:
    cred = await _make_personal(adb, cred_id="cred_ok_p01")
    owner = _identity(user_id=OWNER_ID, department_id=OWNER_DEP, roles=["reader"])

    patcher, emitted = _collect_emits()
    with patcher:
        loaded = await credential_service.load_for_action(adb, owner, cred.id, "read")

    assert loaded.id == cred.id
    assert _denied_calls(emitted) == []


# ── Sanity: missing cred (физическое отсутствие) — emit не нужен ──────────


@pytest.mark.asyncio
async def test_missing_cred_does_not_emit(adb) -> None:
    actor = _identity()
    patcher, emitted = _collect_emits()
    with patcher:
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(
                adb, actor, "cred_doesnotexist", "read"
            )
    # cred физически отсутствует — это не маскировка прав; SOC не нужен такой
    # event, и он отделяет «реальный 404» от «info-leak 404».
    assert _denied_calls(emitted) == []
