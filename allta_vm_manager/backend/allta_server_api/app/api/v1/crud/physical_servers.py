# app/api/v1/crud/physical_servers.py
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.v1.models.physical_servers import PhysicalServer
from app.api.v1.models.os_versions import OSVersion
from app.api.v1.schemas.physical_servers import (
    PhysicalServerCreate,
    PhysicalServerUpdate,
)
from app.utils.crypto import Crypto  # <<< импортируем

def _decrypt_credentials(server: PhysicalServer):
    """
    Дешифрует пароли сразу на объекте ORM-памяти.
    Вызываем в get_* перед возвратом.
    """
    if server.admin_panel_pass:
        server.admin_panel_pass = Crypto.decrypt(server.admin_panel_pass)
    # если в будущем добавятся другие поля — дешифруем их тут

def get_physical_server(db: Session, server_id: int) -> Optional[PhysicalServer]:
    srv = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if srv:
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
    os_id = payload.get("os_version_id")
    if os_id is not None and not db.get(OSVersion, os_id):
        raise ValueError(f"OSVersion with id={os_id} not found")
    payload["admin_panel_pass"] = Crypto.encrypt(payload["admin_panel_pass"])
    server = PhysicalServer(**payload)
    db.add(server)
    try:
        db.commit()
        db.refresh(server)
    except IntegrityError as e:
        db.rollback()
        msg = e.orig.diag.message_detail or str(e)
        raise ValueError(f"Failed to create server: {msg}")
    _decrypt_credentials(server)
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
    # IP → str
    if "ip_address" in update_data:
        update_data["ip_address"] = str(update_data["ip_address"])
    # OSVersion
    if "os_version_id" in update_data and update_data["os_version_id"] is not None:
        os_id = update_data["os_version_id"]
        if not db.get(OSVersion, os_id):
            raise ValueError(f"OSVersion with id={os_id} not found")
    # Шифруем пароль, если передан
    if "admin_panel_pass" in update_data:
        update_data["admin_panel_pass"] = Crypto.encrypt(update_data["admin_panel_pass"])

    for field, val in update_data.items():
        setattr(server, field, val)

    try:
        db.commit()
        db.refresh(server)
    except IntegrityError as e:
        db.rollback()
        msg = e.orig.diag.message_detail or str(e)
        raise ValueError(f"Failed to update server: {msg}")

    # расшифруем перед возвратом
    _decrypt_credentials(server)
    return server

def delete_physical_server(db: Session, server_id: int) -> bool:
    server = db.get(PhysicalServer, server_id)
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
