from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

import uuid

from app.db.session import get_async_db
from app.utils.server_api import get_physical_server_from_remote, set_server_status, clear_server_status

from app.api.v1.dependencies import get_current_admin_user, get_token
from app.utils.config import settings
# from app.tasks.vm_tasks import prepare_server_task  # <- когда подключите Celery

router = APIRouter(
    prefix="/servers",
    tags=["Server"],
)

VMS_HUB_STATUS = settings.VMS_HUB_STATUS

@router.post(
    "/{server_id}/prepare-vms-hub",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Подготовить сервер как VMS hub",
)
async def prepare_vms_hub(
    server_id: int,
    token: str = Depends(get_token),  
    admin=Depends(get_current_admin_user),
):
    
    server = await get_physical_server_from_remote(server_id, token)   
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if not server.virtualization:
        raise HTTPException(status_code=400, detail="Virtualization is disabled on this server")

    updated = await set_server_status(server_id, VMS_HUB_STATUS, token)
    task_id = str(uuid.uuid4())
    return {
        "detail": "Server prepared as VMS hub",
        "task_id": task_id,
        "server": updated.model_dump(),
    }


@router.delete(
    "/{server_id}/rm-vms-hub",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить VMS HUB",
)
async def rm_vms_hub(
    server_id: int,
    token: str = Depends(get_token),  
    admin=Depends(get_current_admin_user),    
):
    server = await get_physical_server_from_remote(server_id, token)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")    
    if server.status != VMS_HUB_STATUS:
        raise HTTPException(status_code=400, detail="This server is not configured as a VMS hub.")
    updated = await clear_server_status(server_id, token)  
    task_id = str(uuid.uuid4())
    return{
        "detail": "Server removed as VMS hub",
        "task_id": task_id,       
    }


