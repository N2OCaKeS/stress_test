from sqlalchemy.orm import Session

from app.api.v1.models.vms import VM
from app.api.v1.schemas.vms import (
    VMCreate,
    VMUpdate
)

def get_vm(db: Session, vm_id: int) -> VM | None:
    return db.query(VM).filter_by(id=vm_id).first()

def get_vms(
    db: Session,
    skip: int = 0,
    limit: int = 100
) -> list[VM]:
    return db.query(VM).offset(skip).limit(limit).all()

def create_vm(
    db: Session,
    obj_in: VMCreate
) -> VM:
    db_obj = VM(**obj_in.model_dump())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def update_vm(
    db: Session,
    db_obj: VM,
    obj_in: VMUpdate
) -> VM:
    data = obj_in.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(db_obj, k, v)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_vm(db: Session, vm_id: int) -> None:
    obj = db.query(VM).filter_by(id=vm_id).first()
    if obj:
        db.delete(obj)
        db.commit()
