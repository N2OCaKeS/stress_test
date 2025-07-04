
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.v1.models.virtual_machine import VirtualMachine
from app.api.v1.schemas.virtual_machine import VMCreate, VMUpdate


def get_vm(db: Session, vm_id: int) -> Optional[VirtualMachine]:
    return db.query(VirtualMachine).filter(VirtualMachine.id == vm_id).first()


def get_vms(db: Session, skip: int = 0, limit: int = 100) -> List[VirtualMachine]:
    return db.query(VirtualMachine).offset(skip).limit(limit).all()


def create_vm(db: Session, data: VMCreate) -> VirtualMachine:
    vm = VirtualMachine(**data.model_dump())
    db.add(vm)
    try:
        db.commit()
        db.refresh(vm)
    except IntegrityError as e:
        db.rollback()
        raise ValueError(f"Failed to create VM: {str(e)}")
    return vm


def update_vm(db: Session, vm_id: int, data: VMUpdate) -> Optional[VirtualMachine]:
    vm = get_vm(db, vm_id)
    if not vm:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(vm, field, value)

    try:
        db.commit()
        db.refresh(vm)
    except IntegrityError as e:
        db.rollback()
        raise ValueError(f"Failed to update VM: {str(e)}")
    return vm


def delete_vm(db: Session, vm_id: int) -> bool:
    vm = get_vm(db, vm_id)
    if not vm:
        return False
    db.delete(vm)
    db.commit()
    return True