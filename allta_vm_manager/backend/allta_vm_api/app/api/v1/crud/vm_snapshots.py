from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.v1.models.vm_snapshot import VMSnapshot
from app.api.v1.schemas.vm_snapshot import VMSnapshotCreate, VMSnapshotUpdate


def get_snapshot(db: Session, snapshot_id: int) -> Optional[VMSnapshot]:
    return db.query(VMSnapshot).filter(VMSnapshot.id == snapshot_id).first()


def get_snapshots(db: Session, vm_id: Optional[int] = None) -> List[VMSnapshot]:
    q = db.query(VMSnapshot)
    if vm_id is not None:
        q = q.filter(VMSnapshot.vm_id == vm_id)
    return q.all()


def create_snapshot(db: Session, data: VMSnapshotCreate) -> VMSnapshot:
    snapshot = VMSnapshot(**data.model_dump())
    db.add(snapshot)
    try:
        db.commit()
        db.refresh(snapshot)
    except IntegrityError as e:
        db.rollback()
        raise ValueError(f"Failed to create snapshot: {str(e)}")
    return snapshot


def update_snapshot(db: Session, snapshot_id: int, data: VMSnapshotUpdate) -> Optional[VMSnapshot]:
    snapshot = get_snapshot(db, snapshot_id)
    if not snapshot:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(snapshot, field, value)

    try:
        db.commit()
        db.refresh(snapshot)
    except IntegrityError as e:
        db.rollback()
        raise ValueError(f"Failed to update snapshot: {str(e)}")
    return snapshot


def delete_snapshot(db: Session, snapshot_id: int) -> bool:
    snapshot = get_snapshot(db, snapshot_id)
    if not snapshot:
        return False
    db.delete(snapshot)
    db.commit()
    return True
