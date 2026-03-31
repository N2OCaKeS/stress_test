from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.schemas.physical_servers import (
    PhysicalServerRead,
    PhysicalServerCreate,
    PhysicalServerUpdate,
    PhysicalServerStatusUpdate,
    FixedServerStatus,
    is_valid_server_name,
    extract_stand_number,
    suggest_server_name,
)
from app.api.v1.crud.physical_servers import (
    get_physical_server,
    get_physical_server_by_ref,
    get_physical_servers,
    create_physical_server,
    update_physical_server,
    delete_physical_server,
    resolve_physical_server_id,
)
from app.api.v1.dependencies import (
    get_current_user,
    get_current_admin_user,
    AuthVerifyResponse,
)
from app.api.v1.models.physical_servers import PhysicalServer
from app.api.v1.models.os_versions import OSVersion
from app.utils.config import settings

router = APIRouter(
    prefix="/manage",
    tags=["Server"],
)


class ServerOSVersionUpdatePayload(BaseModel):
    server_name: str = Field(..., min_length=1, max_length=100)
    os_version_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("server_name", "os_version_name")
    @classmethod
    def strip_not_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized


def _can_manage_servers(user: AuthVerifyResponse) -> bool:
    return user.has_permission(settings.SERVER_MANAGE_PERMISSION)


def _can_see_admin_panel_passwords(user: AuthVerifyResponse) -> bool:
    return user.has_permission(settings.SERVER_MANAGE_PERMISSION) or user.has_permission(
        settings.VM_MANAGE_PERMISSION
    )


def _to_server_read(server: PhysicalServer, *, reveal_passwords: bool) -> PhysicalServerRead:
    response = PhysicalServerRead.model_validate(server)
    os_name = str(getattr(server, "os_version_name", "") or "").strip() or None
    if not os_name:
        os_name = str(response.os_version or "").strip() or None

    updates: dict[str, object] = {"os_version": os_name}
    if not reveal_passwords:
        updates["admin_panel_pass"] = "***hidden***"

    return response.model_copy(update=updates)


@router.get(
    "/",
    response_model=List[PhysicalServerRead],
    summary="Список всех физических серверов (любой аутентифицированный пользователь)",
)
def list_servers(
    db: Session = Depends(get_db),
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    """
    Возвращает список всех зарегистрированных физических серверов.  
    Доступ: любой аутентифицированный пользователь.
    """
    servers = get_physical_servers(db)
    reveal_passwords = _can_see_admin_panel_passwords(current_user)
    return [
        _to_server_read(server, reveal_passwords=reveal_passwords)
        for server in servers
    ]


@router.get(
    "/name-format/invalid",
    summary="Показать серверы с невалидным именем",
)
def list_servers_with_invalid_name(
    db: Session = Depends(get_db),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    servers = db.query(PhysicalServer).order_by(PhysicalServer.id.asc()).all()
    items: list[dict[str, object]] = []

    for server in servers:
        current_name = str(server.name or "").strip()
        if is_valid_server_name(current_name):
            continue

        items.append(
            {
                "server_id": server.id,
                "current_name": current_name,
                "parsed_stand_number": extract_stand_number(current_name),
                "suggested_name": suggest_server_name(current_name),
            }
        )

    return {"count": len(items), "items": items}


@router.post(
    "/os-version",
    response_model=PhysicalServerRead,
    dependencies=[Depends(get_current_admin_user)],
    summary="Обновить версию ОС сервера по имени сервера и имени версии",
)
def update_server_os_version_by_names(
    data: ServerOSVersionUpdatePayload,
    db: Session = Depends(get_db),
):
    server = (
        db.query(PhysicalServer)
        .filter(PhysicalServer.name == data.server_name)
        .first()
    )
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server '{data.server_name}' not found",
        )

    os_version = (
        db.query(OSVersion)
        .filter(OSVersion.name == data.os_version_name)
        .first()
    )
    if not os_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OS version '{data.os_version_name}' not found",
        )

    # Bind relationship directly to keep response and ORM state consistent.
    server.os_version = os_version
    db.commit()
    db.refresh(server)
    return _to_server_read(server, reveal_passwords=True)


@router.get(
    "/{server_ref}",
    response_model=PhysicalServerRead,
    dependencies=[Depends(get_current_user)],
    summary="Получить сервер по id или имени (любой аутентифицированный пользователь)",
)
def get_server(
    server_ref: str,
    db: Session = Depends(get_db),
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    """
    Возвращает информацию о сервере по его ID или имени.  
    Доступ: любой аутентифицированный пользователь.
    """
    try:
        srv = get_physical_server_by_ref(db, server_ref)
        return _to_server_read(
            srv,
            reveal_passwords=_can_see_admin_panel_passwords(current_user),
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/",
    response_model=PhysicalServerRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin_user)],
    summary="Создать новый сервер (только админ)",
)
def create_server(
    data: PhysicalServerCreate,
    db: Session = Depends(get_db),
):
    """
    Создает новый физический сервер.  
    Доступ: только администратор.
    """
    try:
        return create_physical_server(db, data)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch(
    "/{server_ref}",
    response_model=PhysicalServerRead,
    dependencies=[Depends(get_current_admin_user)],
    summary="Обновить данные сервера (только админ)",
)
def update_server(
    server_ref: str,
    data: PhysicalServerUpdate,
    db: Session = Depends(get_db),
):
    """
    Обновляет данные указанного сервера.  
    Доступ: только администратор.
    """
    try:
        server_id = resolve_physical_server_id(db, server_ref)
        return update_physical_server(db, server_id, data)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete(
    "/{server_ref}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_admin_user)],
    summary="Удалить сервер (только админ)",
)
def delete_server(
    server_ref: str,
    db: Session = Depends(get_db),
):
    """
    Удаляет сервер по его ID.  
    Доступ: только администратор.
    """
    try:
        server_id = resolve_physical_server_id(db, server_ref)
        delete_physical_server(db, server_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/{server_ref}/status",
    summary="Изменить статус сервера",
)
def update_status(
    server_ref: str,
    status_in: PhysicalServerStatusUpdate,
    db: Session = Depends(get_db),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    """
    Изменяет статус сервера.
    Доступ: только пользователи с правом управления серверами.
    """
    try:
        server_id = resolve_physical_server_id(db, server_ref)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    server = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server.status = status_in.status
    db.commit()
    db.refresh(server)

    return {"id": server.id, "status": server.status}


@router.post(
    "/{server_ref}/release",
    response_model=PhysicalServerRead,
    summary="Освободить сервер",
)
def release_status(
    server_ref: str,
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """
    Освобождает сервер (возвращает его в статус `free`).
    Доступ: только пользователи с правом управления серверами.
    """
    try:
        server_id = resolve_physical_server_id(db, server_ref)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    srv = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not srv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    curr = srv.status

    if curr == FixedServerStatus.free.value:
        refreshed = get_physical_server(db, server_id)
        return _to_server_read(refreshed, reveal_passwords=True)

    if curr in {s.value for s in FixedServerStatus}:
        srv.status = FixedServerStatus.free.value
    else:
        srv.status = FixedServerStatus.free.value

    db.commit()
    refreshed = get_physical_server(db, server_id)
    return _to_server_read(refreshed, reveal_passwords=True)
