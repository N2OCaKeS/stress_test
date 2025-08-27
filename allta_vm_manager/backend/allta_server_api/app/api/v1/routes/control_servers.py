from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.crud.physical_servers import get_physical_server
from app.api.v1.schemas.physical_servers import PhysicalServerRead
from app.api.v1.dependencies import get_current_user, AuthVerifyResponse
from app.utils.server_power import ServerPowerService

router = APIRouter(
    prefix="/control",
    tags=["Control"],
)


def _check_power_permission(
    server,
    current_user: AuthVerifyResponse
):
    """
    Проверка прав управления питанием сервера:
    - Администратор имеет полный доступ.
    - Обычный пользователь может управлять сервером только если:
      • сервер свободен (status == "free"), или
      • сервер занят именно этим пользователем (status == login).
    """
    if current_user.is_admin:
        return
    if server.status not in ("free", current_user.login):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )


@router.post(
    "/{server_id}/power/on",
    response_model=PhysicalServerRead,
    summary="Включить физический сервер",
)
def power_on_server(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    """
    Включает указанный физический сервер.  
    Доступ: администратор или пользователь, занявший сервер, либо если сервер свободен.
    """
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    _check_power_permission(server, current_user)

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
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    """
    Выключает указанный физический сервер.  
    Доступ: администратор или пользователь, занявший сервер, либо если сервер свободен.
    """
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    _check_power_permission(server, current_user)

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
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    """
    Перезагружает указанный физический сервер.  
    Доступ: администратор или пользователь, занявший сервер, либо если сервер свободен.
    """
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    _check_power_permission(server, current_user)

    ServerPowerService(server=server)
    ServerPowerService.set_boot_order()

    return server
