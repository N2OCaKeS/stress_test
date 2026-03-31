from typing import List, Optional
from sqlalchemy.orm import Session

from app.api.v1.models.os_versions import OSVersion
from app.api.v1.schemas.os_versions import OSVersionCreate, OSVersionUpdate


def _normalize_repository_urls(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return list(dict.fromkeys(cleaned))


def get_os_version(db: Session, id: int) -> Optional[OSVersion]:
    return db.query(OSVersion).filter(OSVersion.id == id).first()

def get_os_versions(db: Session, skip: int = 0, limit: int = 100) -> List[OSVersion]:
    return db.query(OSVersion).offset(skip).limit(limit).all()

def create_os_version(db: Session, data: OSVersionCreate) -> OSVersion:
    payload = data.model_dump()
    payload["repository_urls"] = _normalize_repository_urls(payload.get("repository_urls"))
    obj = OSVersion(**payload)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

def update_os_version(db: Session, id: int, data: OSVersionUpdate) -> Optional[OSVersion]:
    obj = get_os_version(db, id)
    if not obj:
        return None
    update_data = data.model_dump(exclude_unset=True)
    if "repository_urls" in update_data:
        update_data["repository_urls"] = _normalize_repository_urls(update_data.get("repository_urls"))
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
