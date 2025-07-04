# app/api/v1/crud/ip_range.py

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.v1.models.ip_range import IPRange
from app.api.v1.schemas.ip_range import IPRangeCreate, IPRangeUpdate


def get_ip_range(db: Session, range_id: int) -> Optional[IPRange]:
    return db.query(IPRange).filter(IPRange.id == range_id).first()


def get_ip_ranges(db: Session) -> List[IPRange]:
    return db.query(IPRange).all()


def create_ip_range(db: Session, data: IPRangeCreate) -> IPRange:
    ip_range = IPRange(**data.model_dump())
    db.add(ip_range)
    try:
        db.commit()
        db.refresh(ip_range)
    except IntegrityError as e:
        db.rollback()
        raise ValueError(f"Failed to create IP range: {str(e)}")
    return ip_range


def update_ip_range(db: Session, range_id: int, data: IPRangeUpdate) -> Optional[IPRange]:
    ip_range = get_ip_range(db, range_id)
    if not ip_range:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(ip_range, field, value)

    try:
        db.commit()
        db.refresh(ip_range)
    except IntegrityError as e:
        db.rollback()
        raise ValueError(f"Failed to update IP range: {str(e)}")
    return ip_range


def delete_ip_range(db: Session, range_id: int) -> bool:
    ip_range = get_ip_range(db, range_id)
    if not ip_range:
        return False
    db.delete(ip_range)
    db.commit()
    return True
