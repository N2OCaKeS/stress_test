from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.models.physical_servers import PhysicalServer
from app.api.v1.schemas.physical_servers import extract_stand_number
from app.db.session import get_db


router = APIRouter(
    prefix="/arm",
    tags=["ARM"],
)


def _ram_to_label(ram_total_mb: int | None) -> str:
    if ram_total_mb is None:
        return ""
    if ram_total_mb > 0 and ram_total_mb % 1024 == 0:
        return f"{ram_total_mb // 1024}Gb"
    return f"{ram_total_mb}Mb"


def _cpu_to_label(server: PhysicalServer) -> str:
    cpu_model = str(server.cpu_model or "").strip()
    if cpu_model:
        return cpu_model

    cores = server.cpu_cores_count if server.cpu_cores_count is not None else server.cpu_total
    threads = server.cpu_threads if server.cpu_threads is not None else cores
    if cores is None:
        return ""
    return f"{cores} cores / {threads} threads"


@router.get(
    "/",
    summary="Список ARM конфигураций (данные из БД, без кредов)",
)
def list_arm_catalog(db: Session = Depends(get_db)):
    """
    Возвращает ARM-каталог по серверам из БД.
    Формат совместим с текущим (grade/cpu/ram/storage) и дополнен полями железа.
    """
    servers = db.query(PhysicalServer).order_by(PhysicalServer.id.asc()).all()

    items: list[tuple[int, int, str, dict]] = []
    for server in servers:
        stand_number = extract_stand_number(str(server.name or "").strip())
        key = str(stand_number) if stand_number is not None else str(server.id)
        sort_stand = stand_number if stand_number is not None else 10**9

        item = {
            "grade": str(server.grade or "").strip() or "Unknown",
            "cpu": _cpu_to_label(server),
            "ram": _ram_to_label(server.ram_total),
            "storage": str(server.storage or "").strip(),
            "cpu_cores_count": server.cpu_cores_count if server.cpu_cores_count is not None else server.cpu_total,
            "cpu_threads": server.cpu_threads,
            "ram_total": server.ram_total,
            "gpu": str(server.gpu or "").strip() or None,
            "phy_if": server.phy_if,
            "virtualization": bool(server.virtualization),
            "name": server.name,
            "id": server.id,
            "ip_address": str(server.ip_address),
            "status": server.status,
        }
        items.append((sort_stand, server.id, key, item))

    catalog: dict[str, dict] = {}
    for _sort_stand, _server_id, key, payload in sorted(items, key=lambda x: (x[0], x[1])):
        catalog[key] = payload
    return catalog
