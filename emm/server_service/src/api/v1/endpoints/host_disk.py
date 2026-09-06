"""Заполняемость диска на хосте, где крутится сам server_service.

Портирует `server_diskspace_used()` из legacy `allta_app` (ветка `allta_app`,
`allta_app/allta_app/libs/liballta.py`): там `df -h` дёргался локально на
Flask-хосте для `/` (system), `/srv/ftp` (ftp) и `/home/partimag` (partimag).
Здесь то же самое через stdlib `shutil.disk_usage` — без subprocess.

emm рассчитан на тот же физический хост, что и ACS/DRBL/Clonezilla-сервер
(owner-confirmed), поэтому это НЕ SSH-инвентаризация удалённого сервера из
таблицы `servers` (см. `schemas/disk.py` / `ServerDisk` — другой домен,
инвентарь managed test-серверов). Это прямой локальный stat процесса.

Каждый путь читается независимо и никогда не роняет запрос: в dev/compose
хостовые пути `/srv/ftp` и `/home/partimag` обычно не существуют — это
штатный `available=false`, а не 5xx.
"""

import os
import shutil

from fastapi import APIRouter

from src.dependencies.auth import AuthenticatedIdentity
from src.schemas.host_disk import HostDiskPathUsage, HostDiskUsageResponse

router = APIRouter(prefix="/host")

# label -> путь, который реально стат'ится процессом. В k8s (см.
# `k8s/50-server-service.yaml`) под read-only hostPath смонтированы
# `/host-root`, `/host-srv-ftp`, `/host-home-partimag` — соответствующие
# env-переменные переопределяют путь стата, оставляя label исходным
# (человекочитаемым) для UI. Без переопределения (локальный запуск,
# docker-compose) стат идёт по тем же путям, что и label — совпадает с
# legacy-поведением 1:1 на хосте, где сервис реально живёт на голом железе.
WATCHED_HOST_DISK_PATHS: list[tuple[str, str]] = [
    ("/", os.environ.get("HOST_DISK_STAT_PATH_ROOT", "/")),
    ("/srv/ftp", os.environ.get("HOST_DISK_STAT_PATH_FTP", "/srv/ftp")),
    ("/home/partimag", os.environ.get("HOST_DISK_STAT_PATH_PARTIMAG", "/home/partimag")),
]


def _usage_for(label: str, stat_path: str) -> HostDiskPathUsage:
    try:
        total, used, _free = shutil.disk_usage(stat_path)
    except FileNotFoundError:
        return HostDiskPathUsage(path=label, available=False, error="not_mounted")
    except PermissionError:
        return HostDiskPathUsage(path=label, available=False, error="permission_denied")
    except OSError:
        return HostDiskPathUsage(path=label, available=False, error="unavailable")
    return HostDiskPathUsage(
        path=label,
        total_gb=total / 1e9,
        used_gb=used / 1e9,
        used_percent=round(used / total * 100, 1) if total else 0.0,
        available=True,
        error=None,
    )


@router.get(
    "/diskspace",
    response_model=HostDiskUsageResponse,
    summary="Заполняемость диска на хосте server_service (/, /srv/ftp, /home/partimag)",
    description=(
        "Локальный `shutil.disk_usage` по путям, отслеживаемым на хосте самой "
        "платформы (не по managed test-серверам). Не бьётся о БД, доступен "
        "любому аутентифицированному актору — данные не привязаны к отделу "
        "и не содержат ничего чувствительнее объёма диска.\n\n"
        "Каждый путь деградирует независимо (`available=false` + `error`), "
        "если недоступен — например, в dev/docker-compose, где реальные "
        "хостовые точки монтирования не существуют."
    ),
    responses={
        200: {"description": "Список путей с их заполняемостью (даже если часть недоступна)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID — запрос без валидного bearer'а."},
    },
)
async def get_host_diskspace(identity: AuthenticatedIdentity) -> HostDiskUsageResponse:
    """Диски хоста платформы. Любой аутентифицированный актор, без DB-обращений."""
    return HostDiskUsageResponse(
        paths=[_usage_for(label, stat_path) for label, stat_path in WATCHED_HOST_DISK_PATHS],
    )
