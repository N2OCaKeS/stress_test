"""Модель RoleACL — per-credential доступ внутри одного департамента.

`(cred_id, dept_id, role_name)` уникально: одна роль в одном dep'е имеет
ровно одну запись прав. `can_read`/`can_write` — фактические разрешения.

`granted_by_user_id` намеренно без cascade'а на auth.users — если granter
удалён, выданное им разрешение остаётся в силе (симметрия с auth W31
owner-decision). Cred же удаляется CASCADE — без кред ACL бессмысленен.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class RoleACL(Base):
    """Доступ роли департамента к конкретной credential."""

    __tablename__ = "role_acls"

    # id формата `acl_<32 hex>`.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    cred_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Soft-FK на auth.departments.id (`dep_<hex>`).
    dept_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Имя роли из auth.service_role_definitions (per-department каталог).
    role_name: Mapped[str] = mapped_column(String(64), nullable=False)

    can_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Soft-FK на auth identity (`usr_…`/`bot_…`). Без cascade'а — решение
    # остаётся в силе даже если granter удалён.
    granted_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "cred_id", "dept_id", "role_name", name="uq_role_acl_cred_dept_role"
        ),
    )
