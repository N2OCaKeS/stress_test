from typing import List

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
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
    AuthVerifyResponse
)

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
    current_user = Depends(get_current_user),
):
    return get_physical_servers(db)


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
    return create_physical_server(db, data)


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
    srv = get_physical_server(db, server_id)
    if not srv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")
    updated = update_physical_server(db, server_id, data)
    return updated


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
    ok = delete_physical_server(db, server_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")


@router.post(
    "/{server_id}/status",
    response_model=PhysicalServerRead,
    summary="Установить статус сервера (take/free)"
)
def change_status(
    server_id: int,
    data: PhysicalServerStatusUpdate,
    current_user: AuthVerifyResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    srv = get_physical_server(db, server_id)
    if not srv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    new_status = data.status

    if new_status in {s.value for s in FixedServerStatus}:
        if new_status != FixedServerStatus.free.value and not current_user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required for this status")
        srv.status = new_status
    else:
        if new_status != current_user.login and not current_user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot set status on behalf of another user")
        srv.status = new_status

    db.commit()
    db.refresh(srv)
    return srv


@router.post(
    "/{server_id}/release",
    response_model=PhysicalServerRead,
    summary="Освободить сервер"
)
def release_status(
    server_id: int,
    current_user: AuthVerifyResponse = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    srv = get_physical_server(db, server_id)
    if not srv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    curr = srv.status

    # если уже free — возвращаем без изменений
    if curr == FixedServerStatus.free.value:
        return srv

    # если fixed статус отличный от free — только админ
    if curr in {s.value for s in FixedServerStatus}:
        if not current_user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required to change this status")
        srv.status = FixedServerStatus.free.value

    else:
        # статус-username: только владелец или админ
        if curr != current_user.login and not current_user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot release status held by another user")
        srv.status = FixedServerStatus.free.value

    db.commit()
    db.refresh(srv)
    return srv