"""ORM-модель `Department` — отдел/подразделение. Все «привязки к отделу» каскадятся отсюда."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
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

    users: Mapped[list["User"]] = relationship("User", back_populates="department")  # noqa: F821
    service_access: Mapped[list["DepartmentServiceAccess"]] = relationship(  # noqa: F821
        "DepartmentServiceAccess", back_populates="department"
    )
    role_definitions: Mapped[list["ServiceRoleDefinition"]] = relationship(  # noqa: F821
        "ServiceRoleDefinition", back_populates="department", cascade="all, delete-orphan"
    )
    user_groups: Mapped[list["UserGroup"]] = relationship(  # noqa: F821
        "UserGroup", back_populates="department", cascade="all, delete-orphan"
    )
    bots: Mapped[list["BotAccount"]] = relationship("BotAccount", back_populates="department")  # noqa: F821
    oauth_clients: Mapped[list["OAuthClient"]] = relationship("OAuthClient", back_populates="department")  # noqa: F821
    docker_registry: Mapped["DepartmentDockerRegistry | None"] = relationship(  # noqa: F821
        "DepartmentDockerRegistry", back_populates="department", uselist=False
    )
