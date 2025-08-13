from typing import Dict, Tuple, List, Optional
from ipaddress import ip_address

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.models.vm import VirtualMachine
from app.api.v1.models.ip_range import IPRange
from app.api.v1.schemas.vm_batch import BatchVMItem
from app.utils.server_api import get_physical_server_from_remote
from app.utils.config import settings

VMS_HUB_STATUS = settings.VMS_HUB_STATUS

async def get_ip_range(db: AsyncSession, ip_range_id: int) -> Optional[IPRange]:
    res = await db.execute(select(IPRange).where(IPRange.id == ip_range_id))
    return res.scalar_one_or_none()

def ip_in_range(ip: str, ip_start: str, ip_end: str) -> bool:
    """Проверка включения IP в полуинтервал [start..end]."""
    ipn = ip_address(str(ip))
    return ip_address(str(ip_start)) <= ipn <= ip_address(str(ip_end))

async def is_ip_free(db: AsyncSession, ip: str) -> bool:
    """Проверка, что IP не используется в таблице vm."""
    res = await db.execute(
        select(VirtualMachine).where(VirtualMachine.ip_address == str(ip))
    )
    return res.scalar_one_or_none() is None

def get_range_bounds(ipr: IPRange) -> Tuple[str, str]:
    """Достаём границы диапазона из твоей модели."""
    return str(ipr.ip_start), str(ipr.ip_end)

async def is_name_free(db: AsyncSession, name: str) -> bool:
    res = await db.execute(select(VirtualMachine).where(VirtualMachine.name == name))
    return res.scalar_one_or_none() is None

async def ensure_server_ready_for_vms_hub(server_id: int, token: str) -> Tuple[bool, str | None, object | None]:
    """
    Валидирует сервер через remote API:
      - сервер существует
      - status == VMS_HUB_STATUS (без учёта регистра/пробелов)
      - virtualization == True

    Возвращает: (ok, reason, server_obj)
    """
    server = await get_physical_server_from_remote(server_id, token)
    if not server:
        return False, f"server id={server_id} not found", None

    cur_status = (server.status or "").strip().lower()
    need_status = (VMS_HUB_STATUS or "").strip().lower()
    if cur_status != need_status:
        return False, f"server id={server_id} has status {server.status!r}, expected {VMS_HUB_STATUS!r}", server

    if not getattr(server, "virtualization", False):
        return False, "virtualization is disabled on this server", server

    return True, None, server
