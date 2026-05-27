"""Pydantic-схемы дисков сервера.

Диски живут только внутри карточки сервера (раздел `storage`) — отдельного
CRUD-endpoint'а нет. `DiskSpec` приходит в `ServerCreate`/`ServerUpdate`,
`DiskResponse` отдаётся в `ServerResponse.storage`.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Слот системного диска. Один на сервер — инвариант держит partial-unique индекс.
SYSTEM_SLOT = "system"


class DiskSpec(BaseModel):
    """Один диск в разделе `storage` сервера.

    `slot` — логическое имя устройства (`system`, `disk1`, `diskN`), оно же
    ложится в `server_disks.device_name`. Слот `system` всегда системный;
    для остальных системность задаётся флагом `is_system`.
    """

    slot: str = Field(
        ..., min_length=1, max_length=64,
        description="Слот диска: system / disk1 / diskN. Уникален в рамках сервера.",
    )
    size_gb: int = Field(
        ..., ge=0, description="Размер диска в гигабайтах.",
    )
    model: str | None = Field(
        default=None, max_length=256, description="Модель диска (Samsung SSD 970 EVO, ...).",
    )
    is_system: bool = Field(
        default=False,
        description="Системный диск (root). Не более одного на сервер. Слот `system` системный всегда.",
    )

    @model_validator(mode="after")
    def _system_slot_is_system(self) -> "DiskSpec":
        if self.slot == SYSTEM_SLOT:
            object.__setattr__(self, "is_system", True)
        return self


class DiskResponse(BaseModel):
    """Карточка диска внутри `ServerResponse.storage`."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Disk ID (prefix dsk_).")
    slot: str = Field(description="Слот диска (он же device_name).")
    size_gb: int = Field(description="Размер в гигабайтах.")
    model: str | None = Field(default=None, description="Модель диска.")
    is_system: bool = Field(description="Системный диск.")
    created_at: datetime = Field(description="Когда запись создана.")
    updated_at: datetime = Field(description="Когда запись изменена.")

    @classmethod
    def from_orm_disk(cls, disk) -> "DiskResponse":
        """Собрать ответ из ORM-строки `ServerDisk` (slot == device_name)."""
        return cls(
            id=disk.id,
            slot=disk.device_name,
            size_gb=disk.size_gb,
            model=disk.model,
            is_system=disk.is_system,
            created_at=disk.created_at,
            updated_at=disk.updated_at,
        )
