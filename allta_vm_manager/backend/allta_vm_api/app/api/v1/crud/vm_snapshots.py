# crud/vm_snapshot.py
from sqlalchemy.orm import Session

from app.api.v1.models.vm_snapshots import VMSnapshot
from app.api.v1.schemas.vm_snapshots import (
    VMSnapshotCreate,
    VMSnapshotUpdate
)

def get_vm_snapshot(
    db: Session, snapshot_id: int
) -> VMSnapshot | None:
    return db.query(VMSnapshot).filter_by(id=snapshot_id).first()

def get_vm_snapshots(
    db: Session,
    skip: int = 0,
    limit: int = 100
) -> list[VMSnapshot]:
    return db.query(VMSnapshot).offset(skip).limit(limit).all()

def create_vm_snapshot(
    db: Session,
    obj_in: VMSnapshotCreate
) -> VMSnapshot:
    db_obj = VMSnapshot(**obj_in.model_dump())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def update_vm_snapshot(
    db: Session,
    db_obj: VMSnapshot,
    obj_in: VMSnapshotUpdate
) -> VMSnapshot:
    data = obj_in.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(db_obj, k, v)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_vm_snapshot(db: Session, snapshot_id: int) -> None:
    obj = db.query(VMSnapshot).filter_by(id=snapshot_id).first()
    if obj:
        db.delete(obj)
        db.commit()
