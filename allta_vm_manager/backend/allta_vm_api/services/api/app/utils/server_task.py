from __future__ import annotations
from fastapi import HTTPException, status
from app.utils.server_api import get_physical_server_from_remote
from app.api.v1.schemas.task_payload import ServerTaskInfo

async def build_server_task_info(server_id: int, token: str) -> ServerTaskInfo:
    """
    Берём данные о сервере из удалённого Manage API и приводим к ServerTaskInfo.
    """
    srv = await get_physical_server_from_remote(server_id, token)

    ip = getattr(srv, "admin_panel_ip", None) or getattr(srv, "ip_address", None)
    if not ip:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Remote server payload has no IP (admin_panel_ip/ip_address).",
        )

    username = getattr(srv, "server_user", None) or getattr(srv, "admin_panel_user", None)
    password = getattr(srv, "server_password", None) or getattr(srv, "admin_panel_pass", None)

    if not username or password is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Remote server payload missing server credentials (user/password).",
        )

    return ServerTaskInfo(
        id=getattr(srv, "id", None),
        name=getattr(srv, "name", None),
        ip=str(ip),
        username=username,
        password=password,
        phy_if=getattr(srv, "phys_iface", None),
    )
