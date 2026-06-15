"""Тесты модели CredentialUserACL: UNIQUE(cred, user), FK CASCADE с Credential."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.models import Credential, CredentialUserACL


def _envelope() -> str:
    return "v2$nonce$ct"


def _cred(db, *, id: str = "cred_uacl_test_aaaaaaaaaaaaaaa01") -> Credential:
    c = Credential(
        id=id,
        name="cred-for-user-acl",
        service="jira",
        scope="personal",
        owner_user_id="usr_owner_aaaaaaaaaaaaaaaaaa01",
        owner_user_dept_id="dep_owner_aaaaaaaaaaaaaaaa01",
        secret_encrypted=_envelope(),
        created_by="usr_owner_aaaaaaaaaaaaaaaaaa01",
    )
    db.add(c)
    db.commit()
    return c


def _uacl(
    cred_id: str,
    *,
    id: str = "uacl_test_aaaaaaaaaaaaaaaaaaaa01",
    user_id: str = "usr_grantee_aaaaaaaaaaaaaaaa01",
    can_read: bool = True,
    can_write: bool = False,
) -> CredentialUserACL:
    return CredentialUserACL(
        id=id,
        cred_id=cred_id,
        user_id=user_id,
        can_read=can_read,
        can_write=can_write,
        granted_by_user_id="usr_owner_aaaaaaaaaaaaaaaaaa01",
    )


def test_basic_insert_ok(db) -> None:
    c = _cred(db)
    db.add(_uacl(c.id))
    db.commit()
    row = db.scalar(select(CredentialUserACL))
    assert row.cred_id == c.id
    assert row.user_id == "usr_grantee_aaaaaaaaaaaaaaaa01"
    assert row.can_read is True
    assert row.can_write is False
    assert row.created_at is not None


def test_unique_cred_user_collision(db) -> None:
    c = _cred(db)
    db.add(_uacl(c.id, id="uacl_first_aaaaaaaaaaaaaaaaaa1"))
    db.commit()
    db.add(_uacl(c.id, id="uacl_second_aaaaaaaaaaaaaaaaa1"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_unique_allows_different_users_same_cred(db) -> None:
    c = _cred(db)
    db.add(_uacl(c.id, id="uacl_u1", user_id="usr_a00000000000000000000001"))
    db.add(_uacl(c.id, id="uacl_u2", user_id="usr_b00000000000000000000001"))
    db.commit()
    assert len(db.scalars(select(CredentialUserACL)).all()) == 2


def test_fk_cascade_on_credential_delete(db) -> None:
    c = _cred(db)
    db.add(_uacl(c.id))
    db.commit()
    assert db.scalar(select(CredentialUserACL)) is not None
    db.execute(delete(Credential).where(Credential.id == c.id))
    db.commit()
    assert db.scalar(select(CredentialUserACL)) is None


def test_fk_rejects_unknown_cred(db) -> None:
    db.add(_uacl("cred_does_not_exist_ever_00001"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_soft_fk_accepts_arbitrary_user_and_granter(db) -> None:
    """user_id и granted_by_user_id — soft-FK, любая строка проходит."""
    c = _cred(db)
    acl = CredentialUserACL(
        id="uacl_soft_fk_aaaaaaaaaaaaaaaaa1",
        cred_id=c.id,
        user_id="usr_never_existed_000000000001",
        can_read=True,
        can_write=False,
        granted_by_user_id="usr_deleted_long_ago_000000001",
    )
    db.add(acl)
    db.commit()
