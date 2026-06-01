"""Модель IpmiController (iDRAC/iLO/IPMI/Redfish), 1:1 с сервером."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
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
    endpoint_url: Mapped[str] = mapped_column(String(512), nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    # Формат secrets_service: `v<key>$<nonce>$<ciphertext>`.
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    password_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Помечается на dispatch'е ротации (worker должен apply'ить + verify'ить +
    # дёрнуть callback). До прихода callback'а БД-ciphertext считается «не
    # подтверждённым» — retry в worker_dispatch форсит overwrite вместо
    # sticky'а уже сохранённого, не доехавшего до боксу пароля.
    credentials_pending_apply: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
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
