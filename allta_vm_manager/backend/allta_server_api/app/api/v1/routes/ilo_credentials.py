from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.crud.physical_servers import get_physical_servers
from app.api.v1.dependencies import AuthVerifyResponse, get_current_user
from app.api.v1.models.physical_servers import PhysicalServer
from app.api.v1.schemas.physical_servers import extract_stand_number
from app.api.v1.schemas.ilo import IloCredentialRead
from app.db.session import get_db
from app.utils.config import settings

router = APIRouter(
    prefix="/ilo",
    tags=["ILO"],
)

def _stand_key(server: PhysicalServer) -> str | None:
    name = (server.name or "").strip()
    stand_number = extract_stand_number(name)
    if stand_number is None:
        return None
    return f"stand{stand_number}"


def _can_read_ilo(user: AuthVerifyResponse) -> bool:
    return user.has_permission(settings.SERVER_MANAGE_PERMISSION) or user.has_permission(
        settings.VM_MANAGE_PERMISSION
    )


@router.get(
    "/",
    summary="Получить BMC-креды по данным серверов (ilo endpoint for compatibility)",
    response_model=dict[str, IloCredentialRead],
)
def list_ilo_credentials(
    db: Session = Depends(get_db),
    user: AuthVerifyResponse = Depends(get_current_user),
):
    if not _can_read_ilo(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Insufficient permissions: '{settings.SERVER_MANAGE_PERMISSION}' "
                f"or '{settings.VM_MANAGE_PERMISSION}' required"
            ),
        )

    result: dict[str, IloCredentialRead] = {}
    servers = get_physical_servers(db)

    for server in servers:
        ip = str(server.admin_panel_ip or "").strip()
        username = str(server.admin_panel_user or "").strip()
        password = str(server.admin_panel_pass or "").strip()
        if not ip or not username or not password:
            continue

        key = _stand_key(server)
        if key is None:
            continue

        result[key] = IloCredentialRead(
            ip=ip,
            username=username,
            password=password,
        )

    return result
