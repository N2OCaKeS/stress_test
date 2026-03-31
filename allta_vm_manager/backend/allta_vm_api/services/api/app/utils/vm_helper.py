from __future__ import annotations

from ipaddress import ip_address, IPv4Address, IPv6Address
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.config import settings
from app.utils.server_api import (
    get_physical_server_from_remote,
    get_snapshot_password_by_os_version,
)
from app.api.v1.models.ip_range import IPRange
from app.api.v1.models.vm import VirtualMachine


VMS_HUB_STATUS = settings.VMS_HUB_STATUS  # например, "vms_hub"


async def ensure_server_ready_for_vms_hub(
    server_ref: int | str,
    token: str,
) -> tuple[bool, Optional[str], Optional[object]]:
    """
    Проверяет готовность физического сервера для работы как VMS hub.
    Возвращает кортеж (ok, reason, server).
    """
    try:
        server = await get_physical_server_from_remote(server_ref, token)
    except HTTPException as e:
        return False, f"failed to fetch server: {e.detail}", None
    except Exception as e:
        return False, f"failed to fetch server: {e}", None

    if not server:
        return False, f"server id/name='{server_ref}' not found", None

    if not getattr(server, "virtualization", False):
        return False, "virtualization is disabled on this server", None

    status_val = (getattr(server, "status", "") or "").strip().lower()
    if status_val != str(VMS_HUB_STATUS).strip().lower():
        return False, f"server status is {status_val!r}, expected {VMS_HUB_STATUS!r}", None

    ip = getattr(server, "ip_address", None) or getattr(server, "admin_panel_ip", None)
    if not ip:
        return False, "missing server IP (ip_address/admin_panel_ip)", None

    os_version_name = str(
        getattr(server, "os_version", None) or getattr(server, "os_version_name", None) or ""
    ).strip()
    if not os_version_name:
        return False, "missing os_version on server", None

    try:
        snapshot_password = await get_snapshot_password_by_os_version(os_version_name, token)
    except HTTPException as e:
        return False, f"failed to resolve snapshot password: {e.detail}", None
    except Exception as e:
        return False, f"failed to resolve snapshot password: {e}", None

    if not snapshot_password.ssh_username or snapshot_password.password is None:
        return False, f"missing snapshot credentials for os_version '{os_version_name}'", None

    phy_if = getattr(server, "phy_if", None) or getattr(server, "phys_iface", None)
    if not phy_if:
        return False, "missing physical interface field (phy_if/phys_iface)", None

    return True, None, server


async def get_ip_range(db: AsyncSession, ip_range_id: int) -> Optional[IPRange]:
    """
    Возвращает запись диапазона IP по id или None.
    """
    res = await db.execute(select(IPRange).where(IPRange.id == ip_range_id))
    return res.scalar_one_or_none()


def get_range_bounds(ipr) -> tuple:
    start_raw = getattr(ipr, "ip_start")
    end_raw   = getattr(ipr, "ip_end")
    if start_raw is None or end_raw is None:
        raise HTTPException(status_code=400, detail="Invalid IP range model: missing start/end")
    start = ip_address(str(start_raw))
    end   = ip_address(str(end_raw))
    if start.version != end.version:
        raise HTTPException(status_code=400, detail="IP range start/end have different IP versions")
    if int(start) > int(end):
        raise HTTPException(status_code=400, detail="IP range start is greater than end")
    return start, end


def ip_in_range(ip_str: str, start: IPv4Address | IPv6Address, end: IPv4Address | IPv6Address) -> bool:
    """
    Проверяет, входит ли ip_str в [start, end].
    """
    ip = ip_address(ip_str)
    if ip.version != start.version or ip.version != end.version:
        return False
    ival = int(ip)
    return int(start) <= ival <= int(end)


async def is_ip_free(db: AsyncSession, ip_str: str) -> bool:
    """
    True, если IP не занят ни одной ВМ.
    """
    res = await db.execute(select(VirtualMachine.id).where(VirtualMachine.ip_address == str(ip_str)))
    return res.scalar_one_or_none() is None


async def is_name_free(db: AsyncSession, name: str) -> bool:
    """
    True, если имя ВМ свободно.
    """
    res = await db.execute(select(VirtualMachine.id).where(VirtualMachine.name == name))
    return res.scalar_one_or_none() is None
