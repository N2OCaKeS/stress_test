"""Модель VmIpPool — пул IP-адресов для bridge-ВМ (IPAM).

Пул описывает подсеть реального LAN, из которой раздаются статические адреса
bridge-ВМ: `cidr` + `gateway`/`netmask`/`dns` для провижна статики в госте и
диапазон выдачи `[range_start, range_end]`. Привязка — по отделу
(`department_id`), с опциональным override на конкретный hub-сервер
(`server_id`): у сервера с override приоритет над отдельским пулом.

Учёт занятости IPAM ведёт по БД: свободные адреса — это адреса из диапазона за
вычетом gateway и уже назначенных `vms.ip_address`. Отдельной таблицы резервов
пока нет — источник занятости один (карточки ВМ).
"""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class VmIpPool(Base):
    """Пул IP-адресов bridge-ВМ: подсеть, шлюз/маска/DNS, диапазон выдачи."""

    __tablename__ = "vm_ip_pool"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Подсеть пула (`10.177.103.0/24`) — для валидации, что выданный/заданный
    # адрес принадлежит пулу.
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET, nullable=True)
    netmask: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Список DNS-серверов (провижн статики в госте). JSONB-список строк.
    dns: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    range_start: Mapped[IPv4Address | IPv6Address] = mapped_column(INET, nullable=False)
    range_end: Mapped[IPv4Address | IPv6Address] = mapped_column(INET, nullable=False)
    # Override на конкретный hub-сервер: NULL — пул отдельский (для всех hub'ов
    # отдела), иначе только этот сервер. Soft-FK на servers.id.
    server_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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
        # Имя пула уникально в пределах отдела.
        Index("uq_vm_ip_pool_dept_name", "department_id", "name", unique=True),
    )
