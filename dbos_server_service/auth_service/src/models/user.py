"""ORM-модель `User` — основная сущность юзера (username, password_hash, dept, platform_role, ban-поля)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.constants import UserStatus
from src.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(1024), nullable=False)
    department_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=UserStatus.ACTIVE, nullable=False)
    # Auth-level роль: account_admin / department_admin / null для обычных юзеров
    platform_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    department: Mapped["Department"] = relationship("Department", back_populates="users")  # noqa: F821
    service_roles: Mapped[list["UserServiceRole"]] = relationship(  # noqa: F821
        "UserServiceRole", back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["Session"]] = relationship(  # noqa: F821
        "Session", back_populates="user", cascade="all, delete-orphan"
    )
    personal_access_tokens: Mapped[list["PersonalAccessToken"]] = relationship(  # noqa: F821
        "PersonalAccessToken", back_populates="user", cascade="all, delete-orphan"
    )
    bans: Mapped[list["Ban"]] = relationship(  # noqa: F821
        "Ban", back_populates="user", cascade="all, delete-orphan"
    )
    group_memberships: Mapped[list["UserGroupMembership"]] = relationship(  # noqa: F821
        "UserGroupMembership", back_populates="user", cascade="all, delete-orphan"
    )
