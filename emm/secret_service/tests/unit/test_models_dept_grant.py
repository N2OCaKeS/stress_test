"""Тесты модели DeptGrant: UNIQUE(cred, recipient_dept), FK CASCADE."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.models import Credential, DeptGrant


def _envelope() -> str:
    return "v2$nonce$ct"


def _cross_cred(db, *, id: str = "cred_cross_aaaaaaaaaaaaaaaaaa01") -> Credential:
    c = Credential(
        id=id,
        name="cross-cred",
        service="git",
        scope="cross_department",
        owner_dept_id="dep_owner_aaaaaaaaaaaaaaaa01",
        secret_encrypted=_envelope(),
        created_by="usr_admin0000000000000000000001",
    )
    db.add(c)
    db.commit()
    return c


def _grant(
    cred_id: str,
    *,
    id: str = "dgr_test_aaaaaaaaaaaaaaaaaaaaa1",
    recipient_dept_id: str = "dep_recipient_aaaaaaaaaaaaaaa1",
) -> DeptGrant:
    return DeptGrant(
        id=id,
        cred_id=cred_id,
        recipient_dept_id=recipient_dept_id,
        granted_by_user_id="usr_grantor_aaaaaaaaaaaaaaaa1",
    )


def test_basic_insert_ok(db) -> None:
    c = _cross_cred(db)
    db.add(_grant(c.id))
    db.commit()
    row = db.scalar(select(DeptGrant))
    assert row.cred_id == c.id
    assert row.granted_at is not None


def test_unique_cred_recipient_collision(db) -> None:
    c = _cross_cred(db)
    db.add(_grant(c.id, id="dgr_first00000000000000000001"))
    db.commit()
    db.add(_grant(c.id, id="dgr_second0000000000000000001"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_unique_allows_different_recipients(db) -> None:
    c = _cross_cred(db)
    db.add(
        _grant(c.id, id="dgr_a000000000000000000000001", recipient_dept_id="dep_a000000000000000000001")
    )
    db.add(
        _grant(c.id, id="dgr_b000000000000000000000001", recipient_dept_id="dep_b000000000000000000001")
    )
    db.commit()
    assert len(db.scalars(select(DeptGrant)).all()) == 2


def test_fk_cascade_on_credential_delete(db) -> None:
    c = _cross_cred(db)
    db.add(_grant(c.id))
    db.commit()
    assert db.scalar(select(DeptGrant)) is not None
    db.execute(delete(Credential).where(Credential.id == c.id))
    db.commit()
    assert db.scalar(select(DeptGrant)) is None


def test_fk_rejects_unknown_cred(db) -> None:
    db.add(_grant("cred_does_not_exist_at_all001"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_soft_fk_accepts_arbitrary_recipient_and_granter(db) -> None:
    """recipient_dept_id и granted_by_user_id — soft-FK, любая строка ОК."""
    c = _cross_cred(db)
    g = DeptGrant(
        id="dgr_soft_fk00000000000000000001",
        cred_id=c.id,
        recipient_dept_id="dep_will_be_deleted_later_0001",
        granted_by_user_id="bot_already_deleted_000000001",
    )
    db.add(g)
    db.commit()
