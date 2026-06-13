"""ORM-модель `DepartmentDockerRegistry` — per-department конфиг Docker registry."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

PULL_POLICY_ALL = "all"         # pull открыт всей платформе и анонимам, не только отделу
PULL_POLICY_RESTRICTED = "restricted"   # pull разрешён только юзерам из pull_user_ids


class DepartmentDockerRegistry(Base):
    __tablename__ = "department_docker_registry"
    __table_args__ = (UniqueConstraint("department_id", name="uq_dept_docker_registry"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # "all" — pull открыт любому аутентифицированному юзеру и анониму;
    # "restricted" — только юзерам из pull_user_ids
    pull_policy: Mapped[str] = mapped_column(String(32), default=PULL_POLICY_ALL, nullable=False)
    pull_user_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    push_user_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
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

    department: Mapped["Department"] = relationship("Department", back_populates="docker_registry")  # noqa: F821
