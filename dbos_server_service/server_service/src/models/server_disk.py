"""Модель ServerDisk — диски, подключённые к серверу. Ровно один system disk."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class ServerDisk(Base):
    """Диск сервера: device_name, размер в гигабайтах, модель, флаг системного.

    Управляется только через карточку сервера (раздел `storage`) — отдельного
    CRUD-endpoint'а у дисков нет, изоляция отделов идёт через сервер-родитель.
    """

    __tablename__ = "server_disks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_name: Mapped[str] = mapped_column(String(64), nullable=False)
    size_gb: Mapped[int] = mapped_column(BigInteger, nullable=False)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("server_id", "device_name", name="uq_server_disk_device"),
        # Partial unique: ровно один is_system=true диск на сервер.
        Index(
            "uq_server_system_disk",
            "server_id",
            unique=True,
            postgresql_where="is_system",
        ),
    )

    server: Mapped["Server"] = relationship(  # noqa: F821
        "Server", back_populates="disks"
    )
