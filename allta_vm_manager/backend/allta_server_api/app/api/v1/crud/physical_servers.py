from typing import List, Optional
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from app.api.v1.models.physical_servers import PhysicalServer, DriverType
from app.api.v1.models.os_versions import OSVersion
from app.api.v1.schemas.physical_servers import (
    PhysicalServerCreate,
    PhysicalServerUpdate,
    extract_stand_number,
)
from app.utils.crypto import Crypto


def _decrypt_credentials(server: PhysicalServer):
    if server.admin_panel_pass:
        crypto = Crypto()
        server.admin_panel_pass = crypto.decrypt(server.admin_panel_pass)


def _find_server_with_stand_number(
    db: Session,
    stand_number: int,
    exclude_server_id: Optional[int] = None,
) -> Optional[tuple[int, str]]:
    rows = db.query(PhysicalServer.id, PhysicalServer.name).all()
    for row_id, row_name in rows:
        if exclude_server_id is not None and row_id == exclude_server_id:
            continue
        parsed = extract_stand_number(str(row_name or "").strip())
        if parsed == stand_number:
            return row_id, str(row_name or "").strip()
    return None


def _ensure_unique_stand_number(
    db: Session,
    server_name: str,
    exclude_server_id: Optional[int] = None,
) -> None:
    stand_number = extract_stand_number(server_name)
    if stand_number is None:
        raise ValueError("name must match format 'stand<number>_<server_name>'")

    conflict = _find_server_with_stand_number(
        db=db,
        stand_number=stand_number,
        exclude_server_id=exclude_server_id,
    )
    if conflict:
        conflict_id, conflict_name = conflict
        raise ValueError(
            f"stand number '{stand_number}' is already used by server id={conflict_id} ({conflict_name})"
        )


def _normalize_server_ref(server_ref: int | str) -> tuple[Optional[int], Optional[str]]:
    if isinstance(server_ref, int):
        return server_ref, None

    normalized = str(server_ref).strip()
    if not normalized:
        raise ValueError("Server identifier cannot be empty")

    parsed_id: Optional[int] = int(normalized) if normalized.isdigit() else None
    return parsed_id, normalized


def _find_physical_server_by_ref(
    db: Session,
    server_ref: int | str,
    *,
    with_os_version: bool,
) -> Optional[PhysicalServer]:
    parsed_id, parsed_name = _normalize_server_ref(server_ref)

    query = db.query(PhysicalServer)
    if with_os_version:
        query = query.options(joinedload(PhysicalServer.os_version))

    if parsed_id is not None:
        srv = query.filter(PhysicalServer.id == parsed_id).first()
        if srv:
            return srv

    if parsed_name is not None:
        return query.filter(PhysicalServer.name == parsed_name).first()

    return None


def resolve_physical_server_id(db: Session, server_ref: int | str) -> int:
    srv = _find_physical_server_by_ref(db, server_ref, with_os_version=False)
    if not srv:
        raise ValueError(f"Server with id/name='{server_ref}' not found")
    return int(srv.id)


def get_physical_server_by_ref(db: Session, server_ref: int | str) -> PhysicalServer:
    srv = _find_physical_server_by_ref(db, server_ref, with_os_version=True)
    if not srv:
        raise ValueError(f"Server with id/name='{server_ref}' not found")
    _decrypt_credentials(srv)
    return srv


def get_physical_server(db: Session, server_id: int) -> PhysicalServer:
    return get_physical_server_by_ref(db, server_id)


def get_physical_servers(db: Session, skip: int = 0, limit: int = 100) -> List[PhysicalServer]:
    servers = (
        db.query(PhysicalServer)
        .options(joinedload(PhysicalServer.os_version))
        .offset(skip)
        .limit(limit)
        .all()
    )
    for srv in servers:
        _decrypt_credentials(srv)
    return servers


def create_physical_server(db: Session, data: PhysicalServerCreate) -> PhysicalServer:
    payload = data.model_dump()
    payload["ip_address"] = str(payload["ip_address"])
    payload["name"] = str(payload["name"]).strip()
    payload["grade"] = str(payload["grade"]).strip() if payload.get("grade") is not None else None
    payload["cpu_model"] = (
        str(payload["cpu_model"]).strip() if payload.get("cpu_model") is not None else None
    )
    payload["storage"] = str(payload["storage"]).strip() if payload.get("storage") is not None else None
    payload["gpu"] = str(payload["gpu"]).strip() if payload.get("gpu") is not None else None
    if payload.get("cpu_cores_count") is None:
        payload["cpu_cores_count"] = payload.get("cpu_total")
    if payload.get("cpu_threads") is None:
        payload["cpu_threads"] = payload.get("cpu_cores_count")

    os_id = payload.get("os_version_id")
    if os_id is not None and not db.get(OSVersion, os_id):
        raise ValueError(f"OSVersion with id={os_id} not found")

    if payload.get("driver_type") not in [e.value for e in DriverType]:
        raise ValueError(f"Invalid driver_type: {payload.get('driver_type')}")

    exists = db.execute(
        select(PhysicalServer).where(PhysicalServer.ip_address == payload["ip_address"])
    ).scalar_one_or_none()
    if exists:
        raise ValueError(f"Server with IP {payload['ip_address']} already exists")

    _ensure_unique_stand_number(db=db, server_name=payload["name"])

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

    if "name" in update_data:
        update_data["name"] = str(update_data["name"]).strip()
        _ensure_unique_stand_number(
            db=db,
            server_name=update_data["name"],
            exclude_server_id=server_id,
        )
    if "grade" in update_data and update_data["grade"] is not None:
        update_data["grade"] = str(update_data["grade"]).strip()
    if "cpu_model" in update_data and update_data["cpu_model"] is not None:
        update_data["cpu_model"] = str(update_data["cpu_model"]).strip()
    if "storage" in update_data and update_data["storage"] is not None:
        update_data["storage"] = str(update_data["storage"]).strip()
    if "gpu" in update_data and update_data["gpu"] is not None:
        update_data["gpu"] = str(update_data["gpu"]).strip()
    if "cpu_total" in update_data:
        if "cpu_cores_count" not in update_data or update_data["cpu_cores_count"] is None:
            update_data["cpu_cores_count"] = update_data["cpu_total"]
        if "cpu_threads" not in update_data or update_data["cpu_threads"] is None:
            update_data["cpu_threads"] = update_data["cpu_cores_count"]

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
