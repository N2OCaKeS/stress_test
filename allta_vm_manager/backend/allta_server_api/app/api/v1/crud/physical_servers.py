from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.v1.models.physical_servers import PhysicalServer
from app.api.v1.models.os_versions import OSVersion
from app.api.v1.schemas.physical_servers import (
    PhysicalServerCreate,
    PhysicalServerUpdate,
)


def get_physical_server(db: Session, server_id: int) -> Optional[PhysicalServer]:
    return (
        db.query(PhysicalServer)
          .filter(PhysicalServer.id == server_id)
          .first()
    )


def get_physical_servers(db: Session, skip: int = 0, limit: int = 100) -> List[PhysicalServer]:
    return db.query(PhysicalServer).offset(skip).limit(limit).all()


def create_physical_server(db: Session, data: PhysicalServerCreate) -> PhysicalServer:
    payload = data.model_dump()

    # 1) Приводим IPvAnyAddress → строку
    payload["ip_address"] = str(payload["ip_address"])

    # 2) Если указан os_version_id, проверяем, что такая версия существует
    os_id = payload.get("os_version_id")
    if os_id is not None:
        if not db.query(OSVersion).filter(OSVersion.id == os_id).first():
            raise ValueError(f"OSVersion with id={os_id} not found")

    server = PhysicalServer(**payload)
    db.add(server)
    try:
        db.commit()
        db.refresh(server)
    except IntegrityError as e:
        db.rollback()
        # FK-ошибка или дубликат по уникальным полям
        msg = e.orig.diag.message_detail or str(e)
        raise ValueError(f"Failed to create server: {msg}")
    return server


def update_physical_server(
    db: Session,
    server_id: int,
    data: PhysicalServerUpdate
) -> Optional[PhysicalServer]:
    server = get_physical_server(db, server_id)
    if not server:
        return None

    update_data = data.model_dump(exclude_unset=True)

    # если IP передали как IPvAnyAddress, приводим к строке
    if "ip_address" in update_data:
        update_data["ip_address"] = str(update_data["ip_address"])

    # проверка ос-версии
    if "os_version_id" in update_data and update_data["os_version_id"] is not None:
        os_id = update_data["os_version_id"]
        if not db.query(OSVersion).filter(OSVersion.id == os_id).first():
            raise ValueError(f"OSVersion with id={os_id} not found")

    for field, val in update_data.items():
        setattr(server, field, val)

    try:
        db.commit()
        db.refresh(server)
    except IntegrityError as e:
        db.rollback()
        msg = e.orig.diag.message_detail or str(e)
        raise ValueError(f"Failed to update server: {msg}")

    return server


def delete_physical_server(db: Session, server_id: int) -> bool:
    server = get_physical_server(db, server_id)
    if not server:
        return False
    db.delete(server)
    db.commit()
    return True


def occupy_physical_server(
    db: Session,
    server_id: int,
    user_id: int
) -> Optional[PhysicalServer]:
    server = get_physical_server(db, server_id)
    if not server:
        return None
    if server.occupied_by is not None:
        return server
    server.occupied_by = user_id
    db.commit()
    db.refresh(server)
    return server


def release_physical_server(
    db: Session,
    server_id: int,
    user_id: int,
    is_admin: bool
) -> Optional[PhysicalServer]:
    server = get_physical_server(db, server_id)
    if not server:
        return None
    # отпустить могут только тот, кто занял, или админ
    if server.occupied_by is None or (server.occupied_by != user_id and not is_admin):
        return server
    server.occupied_by = None
    db.commit()
    db.refresh(server)
    return server
