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

router = APIRouter(
    prefix="/manage",
    tags=["Server"],
)


@router.get(
    "/",
    response_model=List[PhysicalServerRead],
    summary="Список всех физических серверов (любой аутентифицированный пользователь)",
)
def list_servers(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Возвращает список всех зарегистрированных физических серверов.  
    Доступ: любой аутентифицированный пользователь.
    """
    return get_physical_servers(db)


@router.get(
    "/{server_id}",
    response_model=PhysicalServerRead,
    dependencies=[Depends(get_current_user)],
    summary="Получить сервер по id (любой аутентифицированный пользователь)",
)
def get_server(
    server_id: int,
    db: Session = Depends(get_db),
):
    """
    Возвращает информацию о сервере по его ID.  
    Доступ: любой аутентифицированный пользователь.
    """
    try:
        srv = get_physical_server(db, server_id)
        return srv
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
    current_user=Depends(get_current_user),
):
    """
    Изменяет статус сервера.  
    Доступ: администратор может установить любой статус.  
    Обычный пользователь может установить только свой логин в качестве статуса.
    """
    server = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not server:
        raise HTTPException(404, detail="Server not found")

    if not current_user.is_admin:
        if status_in.status.lower() != current_user.login.lower():
            raise HTTPException(403, detail="Not allowed to set this status")

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
    current_user: AuthVerifyResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Освобождает сервер (возвращает его в статус `free`).  

    - Если сервер уже свободен, возвращает его без изменений.  
    - Если сервер находится в фиксированном статусе, снять его может только админ.  
    - Если сервер занят конкретным пользователем, он может освободить его сам,  
      в остальных случаях это может сделать только админ.
    """
    srv = db.query(PhysicalServer).filter(PhysicalServer.id == server_id).first()
    if not srv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Server with id={server_id} not found")

    curr = srv.status

    if curr == FixedServerStatus.free.value:
        return srv

    if curr in {s.value for s in FixedServerStatus}:
        if not current_user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin required to change this status")
        srv.status = FixedServerStatus.free.value
    else:
        if curr != current_user.login and not current_user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cannot release status held by another user")
        srv.status = FixedServerStatus.free.value

    db.commit()
    db.refresh(srv)
    return srv
