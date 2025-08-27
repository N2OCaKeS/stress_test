from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_admin_user, get_token
from app.utils.config import settings
from app.utils.server_api import (
    get_physical_server_from_remote,
    set_server_status,
    clear_server_status,
)
from app.utils.redis_queue import enqueue_task
from app.api.v1.schemas.task_payload import TaskEnvelope, ServerTaskInfo
from app.api.v1.crud.vm import get_vms, delete_vm
from app.db.session import get_async_db
from app.api.v1.crud.vm_snapshot import list_snapshots, delete_snapshot

router = APIRouter(prefix="/servers", tags=["Server"])
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
    """
    Подготавливает сервер как VMS hub:
    1. Проверяет наличие сервера и включённую виртуализацию.
    2. Переводит сервер в статус VMS hub.
    3. Формирует задание с данными сервера и ставит его в очередь Redis.
    4. Возвращает task_id и обновлённый объект сервера.
    """
    server = await get_physical_server_from_remote(server_id, token)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if not getattr(server, "virtualization", False):
        raise HTTPException(
            status_code=400, detail="Virtualization is disabled on this server"
        )

    updated = await set_server_status(server_id, VMS_HUB_STATUS, token)

    ip = getattr(server, "ip_address", None)
    if not ip:
        raise HTTPException(
            status_code=502,
            detail="Remote server has no IP field (admin_panel_ip/ip_address).",
        )

    username = getattr(server, "server_user", None) or getattr(
        server, "admin_panel_user", None
    )
    password = getattr(server, "server_password", None) or getattr(
        server, "admin_panel_pass", None
    )
    if not username or password is None:
        raise HTTPException(
            status_code=502,
            detail="Remote server has no credentials (server_user/server_password or admin_panel_user/admin_panel_pass).",
        )

    phys_iface = getattr(server, "phys_iface", None) or getattr(server, "phy_if", None)
    if not phys_iface:
        raise HTTPException(
            status_code=502,
            detail="Remote server has no physical interface field (phys_iface/phy_if).",
        )

    server_info = ServerTaskInfo(
        id=getattr(server, "id", server_id),
        name=getattr(server, "name", None),
        ip=str(ip),
        username=username,
        password=password,
        phy_if=str(phys_iface),
    )
    task_id = str(uuid.uuid4())
    envelope = TaskEnvelope(
        task_id=task_id,
        operation="server.init",
        server=server_info,
        vms=None,
        json_remote_path=f"/opt/allta_vm/jobs/{task_id}.json",
        extra=None,
    )
    await enqueue_task(envelope.model_dump(mode="json"))

    return {
        "task_id": task_id,
        "detail": "Server prepared as VMS hub (task enqueued)",
        "server": updated.model_dump(),
    }


@router.delete(
    "/{server_id}/rm-vms-hub",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Удалить VMS hub и все связанные ВМ",
)
async def rm_vms_hub(
    server_id: int,
    token: str = Depends(get_token),
    admin=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Удаляет сервер как VMS hub:
    1. Проверяет наличие сервера и его статус.
    2. Переводит сервер в статус `free`.
    3. Удаляет все ВМ, привязанные к серверу, и их снимки.
    4. Формирует задание на удаление и ставит его в очередь Redis.
    5. Возвращает task_id и обновлённый объект сервера.
    """
    server = await get_physical_server_from_remote(server_id, token)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    if getattr(server, "status", None) != VMS_HUB_STATUS:
        raise HTTPException(status_code=400, detail="This server is not configured as a VMS hub.")

    updated = await set_server_status(server_id, "free", token)

    vms_to_remove = await get_vms(db) 
    vms_on_server = [vm for vm in vms_to_remove if vm.server_id == server_id]

    if vms_on_server:
        for vm in vms_on_server:
            snapshots = await list_snapshots(db, vm_id=vm.id)
            for snapshot in snapshots[0]:
                await delete_snapshot(db, snapshot_id=snapshot.id)
            await delete_vm(db, vm.id)

    ip = getattr(server, "ip_address", None)
    username = getattr(server, "server_user", None) 
    password = getattr(server, "server_password", None) 
    phys_iface = getattr(server, "phy_if", None) 

    server_info = ServerTaskInfo(
        ip=str(ip),
        username=username,
        password=password,
        phy_if=phys_iface
    )

    task_id = str(uuid.uuid4())
    envelope = TaskEnvelope(
        task_id=task_id,
        operation="server.remove", 
        server=server_info,
        json_remote_path=f"/opt/allta_vm/jobs/{task_id}.json",
        extra=None,
    )
    await enqueue_task(envelope.model_dump(mode="json"))

    return {
        "task_id": task_id,
        "detail": "Server removed as VMS hub, status set to 'free' (task enqueued)",
        "server": updated.model_dump(),
    }
