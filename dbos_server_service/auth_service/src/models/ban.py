"""Ban and lock model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.constants import BanType
from src.db.base import Base


class Ban(Base):
    __tablename__ = "bans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ban_type: Mapped[str] = mapped_column(String(32), nullable=False, default=BanType.PERMANENT)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    banned_by: Mapped[str] = mapped_column(String(64), nullable=False)
    banned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    unbanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unbanned_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="bans")  # noqa: F821
