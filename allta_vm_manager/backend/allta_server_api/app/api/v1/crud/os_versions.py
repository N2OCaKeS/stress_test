from typing import List, Optional
from sqlalchemy.orm import Session

from app.api.v1.models.os_versions import OSVersion
from app.api.v1.schemas.os_versions import OSVersionCreate, OSVersionUpdate

def get_os_version(db: Session, id: int) -> Optional[OSVersion]:
    return db.query(OSVersion).filter(OSVersion.id == id).first()

def get_os_versions(db: Session, skip: int = 0, limit: int = 100) -> List[OSVersion]:
    return db.query(OSVersion).offset(skip).limit(limit).all()

def create_os_version(db: Session, data: OSVersionCreate) -> OSVersion:
    obj = OSVersion(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

def update_os_version(db: Session, id: int, data: OSVersionUpdate) -> Optional[OSVersion]:
    obj = get_os_version(db, id)
    if not obj:
        return None
    update_data = data.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        setattr(obj, field, val)
    db.commit()
    db.refresh(obj)
    return obj

def delete_os_version(db: Session, id: int) -> bool:
    obj = get_os_version(db, id)
    if not obj:
        return False
    db.delete(obj)
    db.commit()
    return True
