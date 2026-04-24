"""Personal access token model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_pat_user_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    # First 12 chars of raw token for user recognition — not a secret
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # Subset of user's allowed_services; cannot exceed user's own permissions
    allowed_services: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="personal_access_tokens")  # noqa: F821
