from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.crud.physical_servers import get_physical_server
from app.api.v1.schemas.physical_servers import PhysicalServerRead
from app.api.v1.dependencies import get_current_user, AuthVerifyResponse

router = APIRouter(
    prefix="/control",
    tags=["Control"],
)


def _check_power_permission(
    server,
    current_user: AuthVerifyResponse
):
    """
    Обычный пользователь может управлять сервером,
    только если он свободен (status == "free")
    или занят этим же пользователем (status == login).
    Админ — всегда.
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
    summary="Включить физический сервер"
)
def power_on_server(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    _check_power_permission(server, current_user)

    # TODO: ваша реальная логика включения (IPMI, Redfish и т.п.)
    # Например: ipmi_client.power_on(server)

    return server


@router.post(
    "/{server_id}/power/off",
    response_model=PhysicalServerRead,
    summary="Выключить физический сервер"
)
def power_off_server(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    _check_power_permission(server, current_user)

    # TODO: ваша реальная логика выключения
    # Например: ipmi_client.power_off(server)

    return server


@router.post(
    "/{server_id}/power/reboot",
    response_model=PhysicalServerRead,
    summary="Перезагрузить физический сервер"
)
def reboot_server(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: AuthVerifyResponse = Depends(get_current_user),
):
    server = get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Server not found")

    _check_power_permission(server, current_user)

    # TODO: ваша реальная логика перезагрузки
    # Например: ipmi_client.reboot(server)

    return server
