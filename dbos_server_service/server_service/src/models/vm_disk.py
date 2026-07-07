"""Модель VmDisk — диск виртуальной машины на hub-сервере.

Диски живут отдельной таблицей (волна 2). Строку заводит create-эндпоинт со
`state=creating`; воркер создаёт qcow2 в пуле `/vms`, подключает его к домену
(`attach-disk --persistent --targetbus virtio --serial <vm>_<disk>`) и пишет
финальные факты (path/target_dev/serial/state) callback'ом. При удалении ВМ
диски сносятся каскадом (FK ondelete=CASCADE).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.constants import VmDiskState
from src.db.base import Base


class VmDisk(Base):
    """Диск ВМ: имя, размер, путь к qcow2, target_dev, serial, ФС/точка монтирования."""

    __tablename__ = "vm_disks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    vm_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("vms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Логическое имя диска, уникальное в пределах ВМ (входит в serial).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    size_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    # Путь к qcow2 в пуле hub'а (`/vms/...`); NULL до создания на боксе.
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Устройство в госте (`vdb`, `vdc`, ...); назначает воркер, NULL до attach'а.
    target_dev: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Serial устройства (`<vm>_<disk>`) — по нему гость находит диск в /dev/disk/by-id.
    serial: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Системный диск ВМ (root). Пользовательские диски — is_system=False.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Файловая система и точка монтирования (опционально — гость форматирует/монтирует).
    fs: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mount: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(
        String(16), default=VmDiskState.CREATING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Имя диска уникально в пределах одной ВМ.
        UniqueConstraint("vm_id", "name", name="uq_vm_disk_name"),
    )

    vm: Mapped["Vm"] = relationship("Vm")  # noqa: F821
