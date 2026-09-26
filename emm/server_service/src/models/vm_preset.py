"""Модель VmPreset — шаблон стандартной ВМ отдела.

Пресет описывает «типовую» ВМ, которую разворачивают на hub'е одной кнопкой
через `POST /servers/{id}/create-default-vms`. Правило deploy-once:

* **bridge-пресет** — разворачивается один раз глобально (несёт уникальный
  статический IP/номер в реальном LAN — по имени пресета отдела он один);
* **nat-пресет** — один раз на каждый hub-сервер (NAT-адрес выдаёт libvirt,
  поэтому такую ВМ можно поднять на нескольких hub'ах отдела).

`fixed_ip`/`number` — «желаемые» значения bridge-станции; при развёртывании они
переносятся в карточку ВМ (номер уникален в рамках отдела, поэтому конфликт
всплывёт на create). Пресеты редактируются в UI под правом `vm_preset_manage`.
"""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from src.core.constants import VmNetworkMode
from src.db.base import Base


class VmPreset(Base):
    """Шаблон стандартной ВМ отдела: ресурсы + сеть + желаемый IP/номер."""

    __tablename__ = "vm_preset"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Имя бокса-образа из FTP-каталога (`vm_station` / single-бокс); nullable —
    # universal-пресет может опускать бокс (дефолт разрешается на create).
    box: Mapped[str | None] = mapped_column(String(128), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    network_mode: Mapped[str] = mapped_column(
        String(16), default=VmNetworkMode.BRIDGE, nullable=False
    )
    # Желаемый статический IP bridge-станции (переносится в карточку ВМ).
    fixed_ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(
        INET, nullable=True
    )
    # Желаемый номер стенда (уникален в рамках отдела — конфликт всплывёт при
    # create ВМ).
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Soft-FK на auth_service identity (`usr_<hex>` / `bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # Имя пресета уникально в пределах отдела.
        Index("uq_vm_preset_dept_name", "department_id", "name", unique=True),
    )
