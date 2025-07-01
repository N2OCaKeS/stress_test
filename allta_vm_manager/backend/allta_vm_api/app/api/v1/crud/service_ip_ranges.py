from sqlalchemy.orm import Session

from app.api.v1.models.service_ip_ranges import ServiceIPRange
from app.api.v1.schemas.service_ip_ranges import (
    ServiceIPRangeCreate,
    ServiceIPRangeUpdate
)

def get_service_ip_range(
    db: Session, range_id: int
) -> ServiceIPRange | None:
    return db.query(ServiceIPRange).filter_by(id=range_id).first()

def get_service_ip_ranges(
    db: Session,
    skip: int = 0,
    limit: int = 100
) -> list[ServiceIPRange]:
    return db.query(ServiceIPRange).offset(skip).limit(limit).all()

def create_service_ip_range(
    db: Session,
    obj_in: ServiceIPRangeCreate
) -> ServiceIPRange:
    db_obj = ServiceIPRange(**obj_in.model_dump())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def update_service_ip_range(
    db: Session,
    db_obj: ServiceIPRange,
    obj_in: ServiceIPRangeUpdate
) -> ServiceIPRange:
    data = obj_in.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(db_obj, k, v)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_service_ip_range(db: Session, range_id: int) -> None:
    obj = db.query(ServiceIPRange).filter_by(id=range_id).first()
    if obj:
        db.delete(obj)
        db.commit()
