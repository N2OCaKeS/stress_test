"""ORM-модель `UserGroup` — группа юзеров внутри отдела (department_id обязателен)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class UserGroup(Base):
    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("department_id", "name", name="uq_dept_user_group_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    department: Mapped["Department"] = relationship("Department", back_populates="user_groups")  # noqa: F821
    memberships: Mapped[list["UserGroupMembership"]] = relationship(  # noqa: F821
        "UserGroupMembership", back_populates="group", cascade="all, delete-orphan"
    )
    bot_memberships: Mapped[list["BotGroupMembership"]] = relationship(  # noqa: F821
        "BotGroupMembership", back_populates="group", cascade="all, delete-orphan"
    )
    service_access: Mapped[list["GroupServiceAccess"]] = relationship(  # noqa: F821
        "GroupServiceAccess", back_populates="group", cascade="all, delete-orphan"
    )
    service_roles: Mapped[list["GroupServiceRole"]] = relationship(  # noqa: F821
        "GroupServiceRole", back_populates="group", cascade="all, delete-orphan"
    )
