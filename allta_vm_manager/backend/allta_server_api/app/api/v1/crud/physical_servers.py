from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from app.api.v1.models.physical_servers import PhysicalServer, DriverType
from app.api.v1.models.os_versions import OSVersion
from app.api.v1.schemas.physical_servers import (
    PhysicalServerCreate,
    PhysicalServerUpdate,
)
from app.utils.crypto import Crypto


def _decrypt_credentials(server: PhysicalServer):
    if server.admin_panel_pass:
        crypto = Crypto()
        server.admin_panel_pass = crypto.decrypt(server.admin_panel_pass)


def get_physical_server(db: Session, server_id: int) -> PhysicalServer:
    srv = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not srv:
        raise ValueError(f"Server with id={server_id} not found")
    _decrypt_credentials(srv)
    return srv


def get_physical_servers(db: Session, skip: int = 0, limit: int = 100) -> List[PhysicalServer]:
    servers = db.query(PhysicalServer).offset(skip).limit(limit).all()
    for srv in servers:
        _decrypt_credentials(srv)
    return servers


def create_physical_server(db: Session, data: PhysicalServerCreate) -> PhysicalServer:
    payload = data.model_dump()
    payload["ip_address"] = str(payload["ip_address"])

    # Проверка OSVersion
    os_id = payload.get("os_version_id")
    if os_id is not None and not db.get(OSVersion, os_id):
        raise ValueError(f"OSVersion with id={os_id} not found")

    # Проверка driver_type
    if payload.get("driver_type") not in [e.value for e in DriverType]:
        raise ValueError(f"Invalid driver_type: {payload.get('driver_type')}")

    # Проверка уникальности IP-адреса
    exists = db.execute(
        select(PhysicalServer).where(PhysicalServer.ip_address == payload["ip_address"])
    ).scalar_one_or_none()
    if exists:
        raise ValueError(f"Server with IP {payload['ip_address']} already exists")

    # Шифрование пароля
    crypto = Crypto()
    payload["admin_panel_pass"] = crypto.encrypt(secret=payload["admin_panel_pass"])

    server = PhysicalServer(**payload)
    db.add(server)

    try:
        db.commit()
        db.refresh(server)
    except IntegrityError as e:
        db.rollback()
        msg = getattr(e.orig, "diag", None)
        detail = msg.message_detail if msg and msg.message_detail else str(e)
        raise ValueError(f"Failed to create server: {detail}")

    _decrypt_credentials(server)
    return server


def update_physical_server(
    db: Session,
    server_id: int,
    data: PhysicalServerUpdate
) -> PhysicalServer:
    server = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not server:
        raise ValueError(f"Server with id={server_id} not found")

    update_data = data.model_dump(exclude_unset=True)

    # Проверка IP
    if "ip_address" in update_data:
        update_data["ip_address"] = str(update_data["ip_address"])
        exists = db.execute(
            select(PhysicalServer).where(
                PhysicalServer.ip_address == update_data["ip_address"],
                PhysicalServer.id != server_id
            )
        ).scalar_one_or_none()
        if exists:
            raise ValueError(f"Server with IP {update_data['ip_address']} already exists")

    # Проверка OSVersion
    if "os_version_id" in update_data and update_data["os_version_id"] is not None:
        os_id = update_data["os_version_id"]
        if not db.get(OSVersion, os_id):
            raise ValueError(f"OSVersion with id={os_id} not found")

    # Проверка driver_type
    if "driver_type" in update_data and update_data["driver_type"] not in [e.value for e in DriverType]:
        raise ValueError(f"Invalid driver_type: {update_data['driver_type']}")

    # Шифрование пароля
    if "admin_panel_pass" in update_data:
        crypto = Crypto()
        update_data["admin_panel_pass"] = crypto.encrypt(update_data["admin_panel_pass"])

    for field, val in update_data.items():
        setattr(server, field, val)

    try:
        db.commit()
        db.refresh(server)
    except IntegrityError as e:
        db.rollback()
        msg = getattr(e.orig, "diag", None)
        detail = msg.message_detail if msg and msg.message_detail else str(e)
        raise ValueError(f"Failed to update server: {detail}")

    _decrypt_credentials(server)
    return server


def delete_physical_server(db: Session, server_id: int) -> bool:
    server = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not server:
        raise ValueError(f"Server with id={server_id} not found")
    db.delete(server)
    db.commit()
    return True


def occupy_physical_server(
    db: Session,
    server_id: int,
    user_id: int
) -> PhysicalServer:
    server = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not server:
        raise ValueError(f"Server with id={server_id} not found")
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
) -> PhysicalServer:
    server = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not server:
        raise ValueError(f"Server with id={server_id} not found")
    if server.occupied_by is None or (server.occupied_by != user_id and not is_admin):
        return server
    server.occupied_by = None
    db.commit()
    db.refresh(server)
    return server
