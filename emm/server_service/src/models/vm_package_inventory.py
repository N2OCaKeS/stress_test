"""Модель VmPackageInventory — снимок установленных пакетов гостя ВМ.

В отличие от серверных пакетов (live-probe без БД, результат в task.result),
для ВМ инвентарь сохраняется: одна строка на ВМ с полным списком в JSONB.
Гость тестовой ВМ несёт заметно меньше пакетов, чем инфраструктурный сервер, и
UI-вкладке удобнее показывать последний известный список сразу, не дожидаясь
свежего probe. `GET /vms/{id}/packages` отдаёт эту строку; `?refresh=true`
диспатчит `vm.list_packages`, а callback `record_vm_packages` перезаписывает её.
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class VmPackageInventory(Base):
    """Последний известный список пакетов гостя ВМ (одна строка на ВМ)."""

    __tablename__ = "vm_package_inventory"

    # PK — сам vm_id: инвентарь один на ВМ, отдельного суррогатного ключа не надо.
    vm_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("vms.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Список пакетов [{"name": ..., "version": ...}, ...]. Перезаписывается
    # целиком на каждый callback — дельту не держим.
    packages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    package_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Откуда снят список (dpkg/rpm) — для UI-подсказки; NULL, если воркер не прислал.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Задача vm.list_packages, чей callback последним записал инвентарь.
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
