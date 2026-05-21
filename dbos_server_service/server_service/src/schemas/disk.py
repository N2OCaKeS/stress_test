"""Pydantic-схемы для эндпоинтов /servers/{server_id}/disks.

Диски привязаны к серверу — изоляция отделов идёт транзитом через
visibility-check сервера-родителя. См. `services/disk_service.py`.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DiskCreate(BaseModel):
    """Тело POST /servers/{server_id}/disks. `server_id` — из path."""

    device_name: str = Field(
        ..., min_length=1, max_length=64,
        description="Имя устройства в OS (sda, nvme0n1, ...). UNIQUE в рамках сервера.",
    )
    size_bytes: int = Field(
        ..., ge=0, description="Размер диска в байтах (от lsblk/smartctl).",
    )
    kind: str | None = Field(
        default=None, max_length=32,
        description="Тип носителя: hdd / ssd / nvme. Free-form, без constraint'а на enum.",
    )
    model: str | None = Field(
        default=None, max_length=256, description="Модель диска (Samsung SSD 970 EVO, ...).",
    )
    is_system: bool = Field(
        default=False,
        description="Системный диск (root). Не более одного на сервер (partial unique).",
    )


class DiskUpdate(BaseModel):
    """Тело PATCH /servers/{server_id}/disks/{disk_id}. Все поля опциональны."""

    device_name: str | None = Field(
        default=None, min_length=1, max_length=64, description="Сменить имя устройства.",
    )
    size_bytes: int | None = Field(default=None, ge=0, description="Обновить размер.")
    kind: str | None = Field(default=None, max_length=32, description="Сменить тип.")
    model: str | None = Field(default=None, max_length=256, description="Сменить модель.")
    is_system: bool | None = Field(default=None, description="Пометить как системный.")


class DiskResponse(BaseModel):
    """Карточка диска в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Disk ID (prefix dsk_).")
    server_id: str = Field(description="FK на servers.id.")
    device_name: str = Field(description="Имя устройства.")
    size_bytes: int = Field(description="Размер в байтах.")
    kind: str | None = Field(default=None, description="Тип носителя.")
    model: str | None = Field(default=None, description="Модель диска.")
    is_system: bool = Field(description="Системный диск.")
    created_at: datetime = Field(description="Когда запись создана.")
    updated_at: datetime = Field(description="Когда запись изменена.")
