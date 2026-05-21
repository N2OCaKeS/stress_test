"""Модель IpmiController (iDRAC/iLO/IPMI/Redfish), 1:1 с сервером."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class IpmiController(Base):
    """Запись о BMC: endpoint + зашифрованные credentials. Один на сервер."""

    __tablename__ = "ipmi_controllers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("servers.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Vendor BMC: idrac / ilo / ipmi_generic. Определяет конкретные
    # Redfish-paths (`/Managers/<vendor-id>`). NOT NULL с default
    # `ipmi_generic`, чтобы существующие записи в БД не ломались.
    bmc_vendor: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="ipmi_generic"
    )
    endpoint_url: Mapped[str] = mapped_column(String(512), nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    # Формат secrets_service: `v<key>$<nonce>$<ciphertext>`.
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    password_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    server: Mapped["Server"] = relationship(  # noqa: F821
        "Server", back_populates="ipmi_controller"
    )
