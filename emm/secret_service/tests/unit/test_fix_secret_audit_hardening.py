"""Фиксы вокруг create-gate, delete info-leak, lifecycle-каскадов, redaction и курсора.

Покрытие:

* create department/cross_department требует admin (reader/guest/operator отбиваются);
* delete department-cred read-only грантополучателем → 404, не 403;
* dept_grant revoke не плодит дублирующий dept_revoke_cascade;
* handle_user_deleted чистит и считает ACL'и в чужих dep'ах;
* handle_dept_service_access_revoked не эмитит пустой cascade и идемпотентен;
* recover-окно читается из settings.recover_window_days;
* _to_identity отбивает пустой sub;
* redaction маскирует `name`;
* keyset-курсор согласован и устойчив к naive-timestamp'у.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.core.exceptions import AuthenticationError, AuthorizationError, NotFoundError
from src.dependencies.auth import Identity, _to_identity
from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.repositories import role_acls as acls_repo
from src.schemas.credentials import CredentialCreate
from src.services import (
    credential_service,
    dept_grant_service,
    lifecycle_service,
    redaction,
)
from tests._helpers import b64


def _identity(roles, *, user_id="usr_h00000000000000000000000001",
              department_id="dep_h0000000000000000000000001",
              platform_role=None) -> Identity:
    return Identity(
        user_id=user_id,
        username="u",
        actor_type="user",
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles} if roles else {},
        is_banned=False,
        platform_role=platform_role,
    )


def _silence_emit(module):
    return patch.object(module.audit_service, "emit", lambda *a, **k: None)


def _capture_emit(module):
    emitted: list[tuple[str, dict]] = []
    p = patch.object(
        module.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    )
    return p, emitted


# ── Fix 1: create-gate admin для dept/cross_dep ──────────────────────────────


@pytest.mark.asyncio
async def test_reader_cannot_create_department_cred(adb) -> None:
    reader = _identity(["reader"], department_id="dep_h0000000000000000000000001")
    payload = CredentialCreate(
        name="dept_by_reader",
        service="jira",
        scope="department",
        secret_b64=b64("x"),
        owner_dept_id="dep_h0000000000000000000000001",
    )
    with _silence_emit(credential_service):
        with pytest.raises(AuthorizationError) as exc:
            await credential_service.create(adb, reader, payload)
    assert exc.value.error_code == "CREDENTIAL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_operator_cannot_create_department_cred(adb) -> None:
    """operator — обычная кастомная роль без admin: общие cred'ы не заводит."""
    operator = _identity(["operator"], department_id="dep_h0000000000000000000000001")
    payload = CredentialCreate(
        name="dept_by_operator",
        service="jira",
        scope="department",
        secret_b64=b64("x"),
        owner_dept_id="dep_h0000000000000000000000001",
    )
    with _silence_emit(credential_service):
        with pytest.raises(AuthorizationError) as exc:
            await credential_service.create(adb, operator, payload)
    assert exc.value.error_code == "CREDENTIAL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_admin_can_create_department_cred(adb) -> None:
    admin = _identity(["admin"], department_id="dep_h0000000000000000000000001")
    payload = CredentialCreate(
        name="dept_by_admin",
        service="jira",
        scope="department",
        secret_b64=b64("x"),
        owner_dept_id="dep_h0000000000000000000000001",
    )
    with _silence_emit(credential_service):
        cred = await credential_service.create(adb, admin, payload)
    assert cred.scope == "department"
    assert cred.owner_dept_id == "dep_h0000000000000000000000001"


@pytest.mark.asyncio
async def test_reader_can_still_create_personal(adb) -> None:
    reader = _identity(["reader"], user_id="usr_h0000000000000000000000pers")
    payload = CredentialCreate(
        name="personal_by_reader",
        service="jira",
        scope="personal",
        secret_b64=b64("x"),
    )
    with _silence_emit(credential_service):
        cred = await credential_service.create(adb, reader, payload)
    assert cred.scope == "personal"
    assert cred.owner_user_id == "usr_h0000000000000000000000pers"


@pytest.mark.asyncio
async def test_dept_admin_can_create_department_cred(adb) -> None:
    dep_admin = _identity(
        [], department_id="dep_h0000000000000000000000001",
        platform_role="department_admin",
    )
    payload = CredentialCreate(
        name="dept_by_depadmin",
        service="jira",
        scope="department",
        secret_b64=b64("x"),
        owner_dept_id="dep_h0000000000000000000000001",
    )
    with _silence_emit(credential_service):
        cred = await credential_service.create(adb, dep_admin, payload)
    assert cred.scope == "department"


# ── Fix 2: delete info-leak — read-only грантополучатель → 404 ───────────────


@pytest.mark.asyncio
async def test_delete_readonly_grantee_gets_404_not_403(adb) -> None:
    """department-cred, actor с RoleACL без can_write → delete отдаёт 404."""
    cred = await cred_repo.create(
        adb,
        id="cred_del_leak01",
        name="leak1",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_del_leak0000000000000000a1",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_del_admin000000000000000a1",
    )
    await acls_repo.create(
        adb,
        id="acl_del_leak01",
        cred_id=cred.id,
        dept_id="dep_del_leak0000000000000000a1",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_del_admin000000000000000a1",
    )
    await adb.commit()

    reader = _identity(["reader"], department_id="dep_del_leak0000000000000000a1")
    with _silence_emit(credential_service):
        with pytest.raises(NotFoundError) as exc:
            await credential_service.delete(adb, reader, cred.id, None)
    assert exc.value.error_code == "CREDENTIAL_NOT_FOUND"


# ── Fix 3: dept_grant revoke без дублирующего cascade-события ────────────────


@pytest.mark.asyncio
async def test_dept_grant_revoke_no_duplicate_cascade_event(adb) -> None:
    cred = await cred_repo.create(
        adb,
        id="cred_dg_rev01",
        name="dgrev1",
        service="jira",
        scope="cross_department",
        owner_user_id=None,
        owner_dept_id="dep_dg_owner000000000000000001",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_dg_admin000000000000000001",
    )
    grant = await grants_repo.create(
        adb, id="dgr_dg_rev01", cred_id=cred.id,
        recipient_dept_id="dep_dg_recip000000000000000001",
        granted_by_user_id="usr_dg_admin000000000000000001",
    )
    await acls_repo.create(
        adb,
        id="acl_dg_rev01",
        cred_id=cred.id,
        dept_id="dep_dg_recip000000000000000001",
        role_name="reader",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_dg_admin000000000000000001",
    )
    await adb.commit()

    # grant_dept — owner-side операция: её несёт dep_admin владеющего dep'а.
    owner_admin = _identity(
        ["admin"], department_id="dep_dg_owner000000000000000001",
        user_id="usr_dg_admin000000000000000001",
        platform_role="department_admin",
    )
    patcher, emitted = _capture_emit(dept_grant_service)
    with patcher:
        await dept_grant_service.revoke(adb, owner_admin, cred.id, grant.id)

    actions = [a for a, _ in emitted]
    assert actions.count("tokens.dept_revoke_cascade") == 0
    revoked = [kw for a, kw in emitted if a == "tokens.dept_grant_revoked"]
    assert len(revoked) == 1
    assert revoked[0]["details"]["cascade_role_acls"] == 1


# ── Fix 4: handle_user_deleted чистит ACL'и в чужих dep'ах ───────────────────


@pytest.mark.asyncio
async def test_user_deleted_revokes_foreign_dept_acl(adb) -> None:
    """Personal-cred с ACL в чужом dep'е → ACL снят, role_acls_revoked > 0."""
    owner_dept = "dep_owndep00000000000000000a1"
    foreign_dept = "dep_foreign0000000000000000a1"
    user_id = "usr_fd_owner0000000000000000a1"
    cred = await cred_repo.create(
        adb,
        id="cred_fd_a01",
        name="fd1",
        service="jira",
        scope="personal",
        owner_user_id=user_id,
        owner_dept_id=None,
        owner_user_dept_id=owner_dept,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by=user_id,
    )
    # ACL в dep'е владельца (легитимный) + ACL в чужом dep'е (должен уйти).
    await acls_repo.create(
        adb, id="acl_fd_own", cred_id=cred.id, dept_id=owner_dept,
        role_name="reader", can_read=True, can_write=False,
        granted_by_user_id=user_id,
    )
    await acls_repo.create(
        adb, id="acl_fd_for", cred_id=cred.id, dept_id=foreign_dept,
        role_name="reader", can_read=True, can_write=False,
        granted_by_user_id=user_id,
    )
    await adb.commit()

    with _silence_emit(lifecycle_service):
        summary = await lifecycle_service.handle_user_deleted(
            adb, user_id, "usr_fd_admin000000000000000a1"
        )

    assert summary["role_acls_revoked"] == 1
    assert summary["errors"] == []
    remaining = await acls_repo.get_for_cred(adb, cred.id)
    assert {a.dept_id for a in remaining} == {owner_dept}
    # Кред'а осталась blocked (в dep'е владельца ещё есть грантополучатель).
    refreshed = await cred_repo.get_by_id(adb, cred.id)
    assert refreshed.status == "blocked"


# ── Fix 7 + 5: handle_dept_service_access_revoked — пустой cascade не эмитим ──


@pytest.mark.asyncio
async def test_dept_revoke_no_cascade_when_nothing_affected(adb) -> None:
    emitted = []
    with patch.object(
        lifecycle_service.audit_service, "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    ):
        summary = await lifecycle_service.handle_dept_service_access_revoked(
            adb, "dep_nothing00000000000000000a1", "secret_service",
            "usr_admin000000000000000000a1",
        )

    assert summary["dept_grants_revoked"] == 0
    assert summary["role_acls_revoked"] == 0
    assert summary["errors"] == []
    assert not any(a == "tokens.dept_revoke_cascade" for a, _ in emitted)


# ── Fix 6: recover-окно из settings ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_recover_window_uses_settings(adb) -> None:
    from src.core.exceptions import DomainValidationError

    cred = await cred_repo.create(
        adb,
        id="cred_rw_a01",
        name="rw1",
        service="jira",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_rw_owner0000000000000000a1",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_dept_deleted",
        created_by="usr_rw_admin0000000000000000a1",
    )
    cred.blocked_at = datetime.now(timezone.utc) - timedelta(days=10)
    await adb.commit()

    admin = _identity(["admin"], department_id="dep_rw_owner0000000000000000a1")

    class _Stub:
        recover_window_days = 5

    # Окно 5 дней (из settings) → блокированная 10 дней назад вне окна.
    with patch.object(credential_service, "get_settings", lambda: _Stub()):
        with _silence_emit(credential_service):
            with pytest.raises(DomainValidationError) as exc:
                await credential_service.recover(adb, admin, cred.id)
    assert exc.value.error_code == "RECOVER_WINDOW_EXPIRED"


# ── Fix 8: _to_identity отбивает пустой sub ──────────────────────────────────


def test_to_identity_rejects_empty_sub() -> None:
    with pytest.raises(AuthenticationError):
        _to_identity({"sub": "", "subject_type": "user"})
    with pytest.raises(AuthenticationError):
        _to_identity({"subject_type": "user"})


def test_to_identity_accepts_valid_sub() -> None:
    ident = _to_identity({"sub": "usr_abc", "subject_type": "user"})
    assert ident.user_id == "usr_abc"


# ── Fix 10: redaction маскирует name ─────────────────────────────────────────


def test_redaction_masks_name() -> None:
    out = redaction.redact_payload({"name": "jira_personal", "scope": "personal"})
    assert out["name"] == "<CREDENTIAL>"
    assert out["scope"] == "personal"


# ── Fix 9: keyset-курсор устойчив к naive-timestamp ──────────────────────────


@pytest.mark.asyncio
async def test_cursor_handles_naive_timestamp(adb) -> None:
    """apply_cursor нормализует naive-timestamp в UTC, не роняет сравнение."""
    for i in range(3):
        await cred_repo.create(
            adb,
            id=f"cred_curs_{i:02d}",
            name=f"curs{i}",
            service="jira",
            scope="personal",
            owner_user_id="usr_curs0000000000000000000a1",
            owner_dept_id=None,
            owner_user_dept_id="dep_curs0000000000000000000a1",
            login=None,
            secret_encrypted="v2$nonce$ct",
            status="active",
            created_by="usr_curs0000000000000000000a1",
        )
    await adb.commit()

    page1 = await cred_repo.list_for_user(adb, "usr_curs0000000000000000000a1", limit=2)
    assert len(page1) == 2
    last = page1[-1]
    # Передаём naive-timestamp (без tzinfo) — apply_cursor должен приравнять к UTC.
    naive_ts = last.created_at.replace(tzinfo=None)
    page2 = await cred_repo.list_for_user(
        adb, "usr_curs0000000000000000000a1", limit=2, cursor=(naive_ts, last.id),
    )
    assert set(c.id for c in page1).isdisjoint(set(c.id for c in page2))
