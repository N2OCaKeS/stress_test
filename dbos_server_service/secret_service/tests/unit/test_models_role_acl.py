"""Тесты модели RoleACL: UNIQUE(cred, dept, role), FK CASCADE с Credential."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.models import Credential, RoleACL


def _envelope() -> str:
    return "v2$nonce$ct"


def _cred(db, *, id: str = "cred_acl_test_aaaaaaaaaaaaaaaa01") -> Credential:
    c = Credential(
        id=id,
        name="cred-for-acl",
        service="jira",
        scope="department",
        owner_dept_id="dep_owner_aaaaaaaaaaaaaaaa01",
        secret_encrypted=_envelope(),
        created_by="usr_admin0000000000000000000001",
    )
    db.add(c)
    db.commit()
    return c


def _acl(
    cred_id: str,
    *,
    id: str = "acl_test_aaaaaaaaaaaaaaaaaaaaa01",
    dept_id: str = "dep_aaaaaaaaaaaaaaaaaaaa01",
    role_name: str = "reader",
    can_read: bool = True,
    can_write: bool = False,
) -> RoleACL:
    return RoleACL(
        id=id,
        cred_id=cred_id,
        dept_id=dept_id,
        role_name=role_name,
        can_read=can_read,
        can_write=can_write,
        granted_by_user_id="usr_grantor_aaaaaaaaaaaaaaaa1",
    )


def test_basic_insert_ok(db) -> None:
    c = _cred(db)
    db.add(_acl(c.id))
    db.commit()
    row = db.scalar(select(RoleACL))
    assert row.cred_id == c.id
    assert row.can_read is True
    assert row.can_write is False
    assert row.granted_at is not None


def test_unique_cred_dept_role_collision(db) -> None:
    c = _cred(db)
    db.add(_acl(c.id, id="acl_first_aaaaaaaaaaaaaaaaaaa01"))
    db.commit()
    db.add(_acl(c.id, id="acl_second_aaaaaaaaaaaaaaaaaa01"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_unique_allows_different_roles_same_dept(db) -> None:
    c = _cred(db)
    db.add(_acl(c.id, id="acl_reader01", role_name="reader"))
    db.add(_acl(c.id, id="acl_writer01", role_name="operator", can_write=True))
    db.commit()
    assert len(db.scalars(select(RoleACL)).all()) == 2


def test_unique_allows_same_role_different_dept(db) -> None:
    c = _cred(db)
    db.add(_acl(c.id, id="acl_dep_a01", dept_id="dep_a00000000000000000000001"))
    db.add(_acl(c.id, id="acl_dep_b01", dept_id="dep_b00000000000000000000001"))
    db.commit()
    assert len(db.scalars(select(RoleACL)).all()) == 2


def test_fk_cascade_on_credential_delete(db) -> None:
    c = _cred(db)
    db.add(_acl(c.id))
    db.commit()
    assert db.scalar(select(RoleACL)) is not None
    db.execute(delete(Credential).where(Credential.id == c.id))
    db.commit()
    assert db.scalar(select(RoleACL)) is None


def test_fk_rejects_unknown_cred(db) -> None:
    db.add(_acl("cred_does_not_exist_ever_00001"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_soft_fk_accepts_arbitrary_dept_and_granter(db) -> None:
    """dept_id и granted_by_user_id — soft-FK, любая строка проходит."""
    c = _cred(db)
    acl = RoleACL(
        id="acl_soft_fk_aaaaaaaaaaaaaaaaaa1",
        cred_id=c.id,
        dept_id="dep_anything_works000000000001",
        role_name="custom",
        can_read=True,
        can_write=False,
        granted_by_user_id="bot_deleted_long_ago_0000000001",
    )
    db.add(acl)
    db.commit()
