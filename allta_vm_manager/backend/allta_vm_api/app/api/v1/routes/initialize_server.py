from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import json
import uuid
from pathlib import Path

from app.db.session import get_async_db
from app.api.v1.dependencies import get_current_admin_user

from app.api.v1.crud.virtual_machine import create_vm
from app.api.v1.schemas.virtual_machine import VMCreate
# from app.tasks.vm_tasks import prepare_server_task  # <- когда подключите Celery

router = APIRouter(
    prefix="/servers",
    tags=["Server Management"],
)


@router.post(
    "/{server_id}/prepare-vms-hub",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Подготовить сервер как VMS hub"
)
async def prepare_vms_hub(
    server_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin=Depends(get_current_admin_user),
):
    server = await get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if not server.virtualization:
        raise HTTPException(status_code=400, detail="Virtualization is disabled on this server")

    # Обновляем статус
    server.status = "vms_hub"
    db.add(server)
    await db.commit()
    await db.refresh(server)

    # Запуск Celery-задачи (пока заглушка)
    # task_id = prepare_server_task.delay(server_id)
    task_id = str(uuid.uuid4())  # пока просто рандомный uuid

    return {"detail": "Server prepared as VMS hub", "task_id": task_id}


@router.post(
    "/{server_id}/create-default-vms",
    status_code=status.HTTP_201_CREATED,
    summary="Создать базовые ВМ"
)
async def create_default_vms(
    server_id: int,
    kernel: str,
    os: str,
    db: AsyncSession = Depends(get_async_db),
    admin=Depends(get_current_admin_user),
):
    server = await get_physical_server(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Путь до файла с базовыми шаблонами ВМ
    json_path = Path("/etc/vm_default_templates.json")
    if not json_path.exists():
        raise HTTPException(status_code=500, detail="Default VM templates file not found")

    # Загружаем шаблоны
    with json_path.open("r") as f:
        templates = json.load(f)

    created_vms = []

    for tmpl in templates:
        vm_data = VMCreate(
            name=tmpl["name"],
            cpu=tmpl["cpu"],
            ram=tmpl["ram"],
            disk=tmpl["disk"],
            os=os,
            kernel=kernel,
            ip_address=None,  # при создании можно не задавать
        )
        vm = await create_vm(db, vm_data)
        created_vms.append(vm.name)

    return {"detail": "VMs created", "created": created_vms}
