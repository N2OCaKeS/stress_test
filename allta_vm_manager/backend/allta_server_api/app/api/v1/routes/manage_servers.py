from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.schemas.physical_servers import (
    PhysicalServerRead,
    PhysicalServerCreate,
    PhysicalServerUpdate,
    PhysicalServerStatusUpdate,
    FixedServerStatus,
)
from app.api.v1.crud.physical_servers import (
    get_physical_server,
    get_physical_servers,
    create_physical_server,
    update_physical_server,
    delete_physical_server,
)
from app.api.v1.dependencies import (
    get_current_user,
    get_current_admin_user,
    AuthVerifyResponse,
)
from app.api.v1.models.physical_servers import PhysicalServer
from app.utils.config import settings

router = APIRouter(
    prefix="/manage",
    tags=["Server"],
)


def _can_manage_servers(user: AuthVerifyResponse) -> bool:
    return user.has_permission(settings.SERVER_MANAGE_PERMISSION)


def _can_see_server_passwords(user: AuthVerifyResponse) -> bool:
    return user.has_permission(settings.SERVER_MANAGE_PERMISSION) or user.has_permission(
        settings.VM_MANAGE_PERMISSION
    )


def _to_server_read(server: PhysicalServer, *, reveal_passwords: bool) -> PhysicalServerRead:
    payload = PhysicalServerRead.model_validate(server).model_dump()
    if not reveal_passwords:
        payload["server_password"] = "***hidden***"
        payload["admin_panel_pass"] = "***hidden***"
    return PhysicalServerRead(**payload)


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
    reveal_passwords = _can_see_server_passwords(current_user)
    return [
        _to_server_read(server, reveal_passwords=reveal_passwords)
        for server in servers
    ]


@router.get(
    "/{server_id}",
    response_model=PhysicalServerRead,
    dependencies=[Depends(get_current_user)],
    summary="Получить сервер по id (любой аутентифицированный пользователь)",
)
def get_server(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    """
    Возвращает информацию о сервере по его ID.  
    Доступ: любой аутентифицированный пользователь.
    """
    try:
        srv = get_physical_server(db, server_id)
        return _to_server_read(
            srv,
            reveal_passwords=_can_see_server_passwords(current_user),
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
    "/{server_id}",
    response_model=PhysicalServerRead,
    dependencies=[Depends(get_current_admin_user)],
    summary="Обновить данные сервера (только админ)",
)
def update_server(
    server_id: int,
    data: PhysicalServerUpdate,
    db: Session = Depends(get_db),
):
    """
    Обновляет данные указанного сервера.  
    Доступ: только администратор.
    """
    try:
        return update_physical_server(db, server_id, data)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_admin_user)],
    summary="Удалить сервер (только админ)",
)
def delete_server(
    server_id: int,
    db: Session = Depends(get_db),
):
    """
    Удаляет сервер по его ID.  
    Доступ: только администратор.
    """
    try:
        delete_physical_server(db, server_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/{server_id}/status",
    summary="Изменить статус сервера",
)
def update_status(
    server_id: int,
    status_in: PhysicalServerStatusUpdate,
    db: Session = Depends(get_db),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    """
    Изменяет статус сервера.
    Доступ: только пользователи с правом управления серверами.
    """
    server = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not server:
        raise HTTPException(404, detail="Server not found")

    server.status = status_in.status
    db.commit()
    db.refresh(server)

    return {"id": server.id, "status": server.status}


@router.post(
    "/{server_id}/release",
    response_model=PhysicalServerRead,
    summary="Освободить сервер",
)
def release_status(
    server_id: int,
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """
    Освобождает сервер (возвращает его в статус `free`).
    Доступ: только пользователи с правом управления серверами.
    """
    srv = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not srv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Server with id={server_id} not found")

    curr = srv.status

    if curr == FixedServerStatus.free.value:
        return srv

    if curr in {s.value for s in FixedServerStatus}:
        srv.status = FixedServerStatus.free.value
    else:
        srv.status = FixedServerStatus.free.value

    db.commit()
    db.refresh(srv)
    return srv
