from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.v1.models.vm_task import VMTask
from app.api.v1.schemas.vm_task import VMTaskCreate, VMTaskUpdate


def get_task(db: Session, uuid: str) -> Optional[VMTask]:
    return db.query(VMTask).filter(VMTask.uuid == uuid).first()


def get_tasks(db: Session, user_id: Optional[int] = None) -> List[VMTask]:
    q = db.query(VMTask)
    if user_id:
        q = q.filter(VMTask.user_id == user_id)
    return q.all()


def create_task(db: Session, data: VMTaskCreate) -> VMTask:
    task = VMTask(**data.model_dump())
    db.add(task)
    try:
        db.commit()
        db.refresh(task)
    except IntegrityError as e:
        db.rollback()
        raise ValueError(f"Failed to create task: {str(e)}")
    return task


def update_task(db: Session, uuid: str, data: VMTaskUpdate) -> Optional[VMTask]:
    task = get_task(db, uuid)
    if not task:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(task, field, value)

    try:
        db.commit()
        db.refresh(task)
    except IntegrityError as e:
        db.rollback()
        raise ValueError(f"Failed to update task: {str(e)}")
    return task


def delete_task(db: Session, uuid: str) -> bool:
    task = get_task(db, uuid)
    if not task:
        return False
    db.delete(task)
    db.commit()
    return True
