"""ORM-модель `DepartmentServiceAccess` — связь «отдел имеет access к сервису».

Сюда же `revoked_at`/`revoked_by` для soft-revoke. Уникальность по
`(department_id, service_name)` — одна активная связь на пару.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class DepartmentServiceAccess(Base):
    __tablename__ = "department_service_access"
    __table_args__ = (
        UniqueConstraint("department_id", "service_name", name="uq_dept_service"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    service_name: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("platform_services.service_name", ondelete="CASCADE"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    granted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    department: Mapped["Department"] = relationship("Department", back_populates="service_access")  # noqa: F821
    service: Mapped["PlatformService"] = relationship(  # noqa: F821
        "PlatformService", back_populates="department_access"
    )
