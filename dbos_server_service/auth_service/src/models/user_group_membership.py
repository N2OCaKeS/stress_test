"""User-to-group membership model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class UserGroupMembership(Base):
    __tablename__ = "user_group_memberships"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_membership"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    added_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    group: Mapped["UserGroup"] = relationship("UserGroup", back_populates="memberships")  # noqa: F821
    user: Mapped["User"] = relationship("User", back_populates="group_memberships")  # noqa: F821
