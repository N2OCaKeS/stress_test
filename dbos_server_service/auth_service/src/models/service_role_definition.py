"""ORM-модель `ServiceRoleDefinition` — определение роли в scope `(dept, service, role_name)`."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class ServiceRoleDefinition(Base):
    __tablename__ = "service_role_definitions"
    __table_args__ = (
        UniqueConstraint(
            "department_id", "service_name", "role_name", name="uq_dept_service_role_name"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    service_name: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("platform_services.service_name", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Системные роли (`admin`) сеются автоматически при grant_service_access.
    # Изменять или удалять через API их нельзя.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    service: Mapped["PlatformService"] = relationship("PlatformService", back_populates="role_definitions")  # noqa: F821
    department: Mapped["Department"] = relationship("Department", back_populates="role_definitions")  # noqa: F821
