"""Справочник OS-версий. servers.os_version_id ссылается сюда."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class OsVersion(Base):
    """OS-версия: уникальное имя + описание + список репозиториев."""

    __tablename__ = "os_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # URL-строки репозиториев версии. Пустой список по умолчанию.
    repositories: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    # Версии ядер, доступные для этой РЦ. Список ведётся вручную, автоматическим
    # резолвом (по аналогии с repositories) пока не покрыт.
    kernels: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    # Срочный хотфикс вне обычного цикла РЦ (legacy UU из allta_app),
    # а не очередной плановый релиз.
    is_urgent_update: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    servers: Mapped[list["Server"]] = relationship(  # noqa: F821
        "Server", back_populates="os_version"
    )
