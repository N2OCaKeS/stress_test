"""Тесты инвариантов модели Credential.

Покрывают БД-CHECK'и (scope vs owner, длина и формат envelope'а),
partial UNIQUE по (owner, service, name) WHERE active, дефолты и
soft-FK на user/dept (нет реального FK, любая строка-id принимается).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import Credential


def _envelope(plaintext: str = "ciphertext-bytes") -> str:
    """Минимально корректный envelope `v<ver>$<nonce>$<ct>` для CHECK."""
    return f"v2$nonceblob$ct-{plaintext}"


def _make_personal(
    *,
    id: str = "cred_personal_aaaaaaaaaaaaaaaaaaaaaa01",
    owner_user_id: str = "usr_aaaaaaaaaaaaaaaaaaaa01",
    name: str = "jira_personal",
    service: str = "jira",
    status: str = "active",
    secret: str | None = None,
) -> Credential:
    return Credential(
        id=id,
        name=name,
        service=service,
        scope="personal",
        owner_user_id=owner_user_id,
        owner_dept_id=None,
        login="alice",
        secret_encrypted=secret or _envelope(),
        status=status,
        created_by="usr_admin0000000000000000000001",
    )


def _make_dept(
    *,
    id: str = "cred_dept_bbbbbbbbbbbbbbbbbbbbbb01",
    owner_dept_id: str = "dep_bbbbbbbbbbbbbbbbbbbb01",
    scope: str = "department",
    name: str = "jira_bot",
    service: str = "jira",
    status: str = "active",
    secret: str | None = None,
) -> Credential:
    return Credential(
        id=id,
        name=name,
        service=service,
        scope=scope,
        owner_user_id=None,
        owner_dept_id=owner_dept_id,
        login="bot",
        secret_encrypted=secret or _envelope(),
        status=status,
        created_by="usr_admin0000000000000000000001",
    )


# ── CHECK: personal ⇒ owner_user_id only ────────────────────────────────────


def test_personal_with_user_owner_ok(db) -> None:
    db.add(_make_personal())
    db.commit()
    row = db.scalar(select(Credential))
    assert row is not None
    assert row.scope == "personal"
    assert row.status == "active"


def test_personal_without_user_owner_fails(db) -> None:
    cred = _make_personal()
    cred.owner_user_id = None
    db.add(cred)
    with pytest.raises(IntegrityError):
        db.commit()


def test_personal_with_dept_owner_fails(db) -> None:
    cred = _make_personal()
    cred.owner_dept_id = "dep_extra000000000000000001"
    db.add(cred)
    with pytest.raises(IntegrityError):
        db.commit()


# ── CHECK: department / cross_department ⇒ owner_dept_id only ──────────────


@pytest.mark.parametrize("scope", ["department", "cross_department"])
def test_dept_scope_with_dept_owner_ok(db, scope: str) -> None:
    db.add(_make_dept(scope=scope))
    db.commit()
    row = db.scalar(select(Credential))
    assert row.scope == scope


@pytest.mark.parametrize("scope", ["department", "cross_department"])
def test_dept_scope_without_dept_owner_fails(db, scope: str) -> None:
    cred = _make_dept(scope=scope)
    cred.owner_dept_id = None
    db.add(cred)
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize("scope", ["department", "cross_department"])
def test_dept_scope_with_user_owner_fails(db, scope: str) -> None:
    cred = _make_dept(scope=scope)
    cred.owner_user_id = "usr_should_not_be_here00000001"
    db.add(cred)
    with pytest.raises(IntegrityError):
        db.commit()


# ── CHECK: secret length < 8192 ─────────────────────────────────────────────


def test_secret_len_under_limit_ok(db) -> None:
    long_secret = "v2$nonce$" + ("x" * 8000)
    db.add(_make_personal(secret=long_secret))
    db.commit()


def test_secret_len_at_limit_fails(db) -> None:
    huge_secret = "v2$nonce$" + ("x" * 8200)
    db.add(_make_personal(secret=huge_secret))
    with pytest.raises(IntegrityError):
        db.commit()


# ── CHECK: envelope regex ^v\d+\$ ───────────────────────────────────────────


def test_secret_envelope_format_ok(db) -> None:
    db.add(_make_personal(secret="v1$nonce$ct"))
    db.commit()
    db.add(
        _make_personal(
            id="cred_personal_aaaaaaaaaaaaaaaaaaaaaa02",
            name="jira_personal_v42",
            secret="v42$n$c",
        )
    )
    db.commit()


def test_secret_envelope_plain_text_fails(db) -> None:
    db.add(_make_personal(secret="plaintext-without-prefix"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_secret_envelope_no_version_digit_fails(db) -> None:
    db.add(_make_personal(secret="v$nonce$ct"))
    with pytest.raises(IntegrityError):
        db.commit()


# ── Partial UNIQUE: (owner, service, name) WHERE active ─────────────────────


def test_two_active_same_owner_service_name_collide(db) -> None:
    db.add(_make_personal(id="cred_personal_aaaaaaaaaaaaaaaaaaaaaa01"))
    db.commit()
    db.add(_make_personal(id="cred_personal_aaaaaaaaaaaaaaaaaaaaaa02"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_active_plus_blocked_same_key_ok(db) -> None:
    """Blocked-запись не блокирует новую active с тем же ключом."""
    blocked = _make_personal(id="cred_blocked01", status="blocked")
    blocked.blocked_at = datetime.now(timezone.utc)
    blocked.blocked_reason = "owner_deleted"
    db.add(blocked)
    db.commit()
    db.add(_make_personal(id="cred_active_a01"))
    db.commit()
    rows = db.scalars(select(Credential)).all()
    assert {r.status for r in rows} == {"active", "blocked"}


def test_dept_partial_unique_independent_of_user_namespace(db) -> None:
    """Personal и department-креды разделяют namespace по COALESCE(user, dept)."""
    db.add(_make_personal(name="shared", service="git"))
    db.commit()
    # тот же name+service но dept-owner — другой ключ COALESCE, ОК
    db.add(_make_dept(name="shared", service="git"))
    db.commit()
    assert db.scalar(select(Credential.id).where(Credential.scope == "department")) is not None


# ── Soft-FK: user/dept id — любая строка принимается (нет реального FK) ─────


def test_soft_fk_accepts_arbitrary_user_id(db) -> None:
    db.add(_make_personal(owner_user_id="usr_nonexistent_anyway_00001"))
    db.commit()


def test_soft_fk_accepts_arbitrary_dept_id(db) -> None:
    db.add(_make_dept(owner_dept_id="dep_nonexistent_anyway_00001"))
    db.commit()


# ── Defaults ────────────────────────────────────────────────────────────────


def test_status_default_active(db) -> None:
    cred = Credential(
        id="cred_default_status_00000000001",
        name="x",
        service="jira",
        scope="personal",
        owner_user_id="usr_aaaaaaaaaaaaaaaaaaaa01",
        secret_encrypted=_envelope(),
        created_by="usr_admin0000000000000000000001",
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    assert cred.status == "active"
    assert cred.created_at is not None
    assert cred.updated_at is not None
