"""Схемы для /host/diskspace — заполняемость диска на хосте самого server_service.

Не путать с `schemas/disk.py` (`ServerDisk` — инвентарь дисков управляемых
серверов, собирается SSH-инвентаризацией воркером). Здесь — локальный
`shutil.disk_usage` по путям на машине, где крутится сам сервис.
"""

from pydantic import BaseModel, Field


class HostDiskPathUsage(BaseModel):
    """Заполняемость одного отслеживаемого пути на хосте платформы."""

    path: str = Field(description="Хост-путь для отображения (не обязательно совпадает с mount-точкой внутри контейнера).")
    total_gb: float | None = Field(default=None, description="Общий объём, ГБ (`total / 1e9`). null, если путь недоступен.")
    used_gb: float | None = Field(default=None, description="Занято, ГБ (`used / 1e9`). null, если путь недоступен.")
    used_percent: float | None = Field(default=None, description="Процент занятости, округлён до 0.1. null, если путь недоступен.")
    available: bool = Field(description="False, если путь не существует или недоступен для чтения.")
    error: str | None = Field(
        default=None,
        description="Машинный код причины недоступности: not_mounted / permission_denied / unavailable. null при успехе.",
    )


class HostDiskUsageResponse(BaseModel):
    """Ответ `GET /host/diskspace` — список отслеживаемых путей хоста."""

    paths: list[HostDiskPathUsage] = Field(description="Заполняемость по каждому пути из WATCHED_HOST_DISK_PATHS.")
