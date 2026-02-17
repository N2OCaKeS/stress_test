from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.crud.physical_servers import get_physical_server
from app.api.v1.schemas.physical_servers import PhysicalServerRead
from app.api.v1.dependencies import AuthVerifyResponse, get_current_admin_user
from app.utils.server_power import ServerPowerService

router = APIRouter(
    prefix="/control",
    tags=["Control"],
)


@router.post(
    "/{server_id}/power/on",
    response_model=PhysicalServerRead,
    summary="Включить физический сервер",
)
def power_on_server(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    """
    Включает указанный физический сервер.
    Доступ: только пользователи с правом управления серверами.
    """
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    ServerPowerService(server=server)
    ServerPowerService.power_on()

    return server


@router.post(
    "/{server_id}/power/off",
    response_model=PhysicalServerRead,
    summary="Выключить физический сервер",
)
def power_off_server(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    """
    Выключает указанный физический сервер.
    Доступ: только пользователи с правом управления серверами.
    """
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    ServerPowerService(server=server)
    ServerPowerService.power_off()    

    return server


@router.post(
    "/{server_id}/power/reboot",
    response_model=PhysicalServerRead,
    summary="Перезагрузить физический сервер",
)
def reboot_server(
    server_id: int,
    db: Session = Depends(get_db),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    """
    Перезагружает указанный физический сервер.
    Доступ: только пользователи с правом управления серверами.
    """
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    ServerPowerService(server=server)
    ServerPowerService.set_boot_order()

    return server
