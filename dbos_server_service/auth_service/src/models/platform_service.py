"""ORM-модель `PlatformService` — регистр платформенных сервисов (`server_service`, `loging_service`...)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class PlatformService(Base):
    __tablename__ = "platform_services"

    # service_name — естественный business key, используется во всех API-URL
    service_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    department_access: Mapped[list["DepartmentServiceAccess"]] = relationship(  # noqa: F821
        "DepartmentServiceAccess", back_populates="service"
    )
    role_definitions: Mapped[list["ServiceRoleDefinition"]] = relationship(  # noqa: F821
        "ServiceRoleDefinition", back_populates="service"
    )
