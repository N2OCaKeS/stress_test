"""Модель DeptGrant — флаг «recipient_dept_id допущен к cross_department-креде».

Без `DeptGrant` локальный dep_admin recipient'а не может выдавать `RoleACL`
на креду, даже если та `scope=cross_department` (см. README §«DeptGrant»).
Удаление кред каскадно сносит и dept-grants — без кред разрешения не нужны.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class DeptGrant(Base):
    """Допуск recipient-департамента к cross_department-креде."""

    __tablename__ = "dept_grants"

    # id формата `dgr_<32 hex>`.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    cred_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Soft-FK на auth.departments.id (`dep_<hex>`).
    recipient_dept_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Soft-FK на auth identity. Без cascade'а — см. RoleACL.
    granted_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "cred_id", "recipient_dept_id", name="uq_dept_grant_cred_recipient"
        ),
    )
