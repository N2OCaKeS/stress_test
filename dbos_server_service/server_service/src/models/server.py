"""Модель Server — основная сущность сервиса."""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.constants import BusyState, PowerState, ServerStatus
from src.db.base import Base


class Server(Base):
    """Карточка сервера + кэш состояний (power/busy) + железная мета."""

    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ip_address: Mapped[IPv4Address | IPv6Address] = mapped_column(
        INET, unique=True, nullable=False, index=True
    )
    mgmt_ip_address: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET, nullable=True)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    os_version_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("os_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    os_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default=ServerStatus.UNKNOWN, nullable=False
    )
    power_state: Mapped[str] = mapped_column(
        String(32), default=PowerState.UNKNOWN, nullable=False
    )
    busy_state: Mapped[str] = mapped_column(
        String(32), default=BusyState.FREE, nullable=False
    )
    busy_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    busy_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    busy_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    asset_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cpu_brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_threads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_frequency_ghz: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_total_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    network_interface_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decommissioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # Композитный индекс под типовой list — «все сервера моего отдела в статусе X».
        Index("ix_servers_department_status", "department_id", "status"),
        # Для быстрого «кто что занял» — busy-dashboard.
        Index("ix_servers_busy_state_user", "busy_state", "busy_user_id"),
    )

    os_version: Mapped["OsVersion | None"] = relationship(  # noqa: F821
        "OsVersion", back_populates="servers"
    )
    account_links: Mapped[list["ServerAccountServer"]] = relationship(  # noqa: F821
        "ServerAccountServer", back_populates="server", cascade="all, delete-orphan"
    )
    ipmi_controller: Mapped["IpmiController | None"] = relationship(  # noqa: F821
        "IpmiController",
        back_populates="server",
        cascade="all, delete-orphan",
        uselist=False,
    )
    disks: Mapped[list["ServerDisk"]] = relationship(  # noqa: F821
        "ServerDisk", back_populates="server", cascade="all, delete-orphan"
    )
