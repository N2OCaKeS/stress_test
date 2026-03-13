from sqlalchemy.orm import Session, joinedload

from app.api.v1.models.os_versions import OSVersion
from app.api.v1.models.snapshot_passwords import SnapshotPassword
from app.api.v1.schemas.snapshot_passwords import SnapshotPasswordCreate, SnapshotPasswordUpdate
from app.utils.crypto import Crypto


class OSVersionNotFoundError(ValueError):
    def __init__(self, os_version_name: str):
        super().__init__(f"OS version '{os_version_name}' not found")
        self.os_version_name = os_version_name


def _decrypt_password(item: SnapshotPassword) -> None:
    if item.password:
        item.password = Crypto().decrypt(item.password)


def _get_os_version_by_name(db: Session, os_version_name: str) -> OSVersion | None:
    return (
        db.query(OSVersion)
        .filter(OSVersion.name == os_version_name)
        .first()
    )


def list_snapshot_passwords(db: Session) -> list[SnapshotPassword]:
    items = (
        db.query(SnapshotPassword)
        .join(OSVersion, SnapshotPassword.os_version_id == OSVersion.id)
        .options(joinedload(SnapshotPassword.os_version))
        .order_by(OSVersion.name.asc())
        .all()
    )
    for item in items:
        _decrypt_password(item)
    return items


def get_snapshot_password(db: Session, os_version_name: str) -> SnapshotPassword | None:
    item = (
        db.query(SnapshotPassword)
        .join(OSVersion, SnapshotPassword.os_version_id == OSVersion.id)
        .options(joinedload(SnapshotPassword.os_version))
        .filter(OSVersion.name == os_version_name)
        .first()
    )
    if not item:
        return None
    _decrypt_password(item)
    return item


def create_or_update_snapshot_password(
    db: Session,
    payload: SnapshotPasswordCreate,
    *,
    updated_by: str | None,
) -> tuple[SnapshotPassword, bool]:
    os_version = _get_os_version_by_name(db, payload.os_version_name)
    if not os_version:
        raise OSVersionNotFoundError(payload.os_version_name)

    existing = (
        db.query(SnapshotPassword)
        .options(joinedload(SnapshotPassword.os_version))
        .filter(SnapshotPassword.os_version_id == os_version.id)
        .first()
    )
    encrypted_password = Crypto().encrypt(payload.password)

    if existing:
        existing.ssh_username = payload.ssh_username
        existing.password = encrypted_password
        existing.updated_by = updated_by
        db.commit()
        db.refresh(existing)
        existing.os_version = os_version
        _decrypt_password(existing)
        return existing, False

    item = SnapshotPassword(
        os_version_id=os_version.id,
        ssh_username=payload.ssh_username,
        password=encrypted_password,
        updated_by=updated_by,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    item.os_version = os_version
    _decrypt_password(item)
    return item, True


def update_snapshot_password(
    db: Session,
    os_version_name: str,
    payload: SnapshotPasswordUpdate,
    *,
    updated_by: str | None,
) -> SnapshotPassword | None:
    os_version = _get_os_version_by_name(db, os_version_name)
    if not os_version:
        raise OSVersionNotFoundError(os_version_name)

    item = (
        db.query(SnapshotPassword)
        .options(joinedload(SnapshotPassword.os_version))
        .filter(SnapshotPassword.os_version_id == os_version.id)
        .first()
    )
    if not item:
        return None

    if payload.password is not None:
        item.password = Crypto().encrypt(payload.password)
    if payload.ssh_username is not None:
        item.ssh_username = payload.ssh_username

    item.updated_by = updated_by
    db.commit()
    db.refresh(item)
    item.os_version = os_version
    _decrypt_password(item)
    return item


def delete_snapshot_password(db: Session, os_version_name: str) -> bool:
    os_version = _get_os_version_by_name(db, os_version_name)
    if not os_version:
        raise OSVersionNotFoundError(os_version_name)

    item = (
        db.query(SnapshotPassword)
        .filter(SnapshotPassword.os_version_id == os_version.id)
        .first()
    )
    if not item:
        return False

    db.delete(item)
    db.commit()
    return True
