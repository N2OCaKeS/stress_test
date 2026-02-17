from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


PERMISSION_DOCKER = "docker"
PERMISSION_PORTAINER = "portainer"
PERMISSION_DEVPI = "devpi"
PERMISSION_CONFIG_TOKENS = "config.tokens"
PERMISSION_SERVER_MANAGE = "server.manage"
PERMISSION_VM_MANAGE = "vm.manage"

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_GUEST = "guest"

GROUP_GUEST = "guest"
GROUP_CONFIG_TOKENS = "config_tokens"
GROUP_INFRA_MANAGERS = "infra_managers"


role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        Integer,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

group_permissions = Table(
    "group_permissions",
    Base.metadata,
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        Integer,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)

    roles = relationship(
        "Role",
        secondary=role_permissions,
        back_populates="permissions",
    )
    groups = relationship(
        "Group",
        secondary=group_permissions,
        back_populates="permissions",
    )


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)

    users = relationship("User", back_populates="role")
    permissions = relationship(
        "Permission",
        secondary=role_permissions,
        back_populates="roles",
    )


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)

    users = relationship(
        "User",
        secondary=user_groups,
        back_populates="groups",
    )
    permissions = relationship(
        "Permission",
        secondary=group_permissions,
        back_populates="groups",
    )
