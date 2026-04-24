"""User group model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class UserGroup(Base):
    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("name", name="uq_user_group_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    memberships: Mapped[list["UserGroupMembership"]] = relationship(  # noqa: F821
        "UserGroupMembership", back_populates="group", cascade="all, delete-orphan"
    )
    service_access: Mapped[list["GroupServiceAccess"]] = relationship(  # noqa: F821
        "GroupServiceAccess", back_populates="group", cascade="all, delete-orphan"
    )
    service_roles: Mapped[list["GroupServiceRole"]] = relationship(  # noqa: F821
        "GroupServiceRole", back_populates="group", cascade="all, delete-orphan"
    )
