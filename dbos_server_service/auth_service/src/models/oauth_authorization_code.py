"""OAuth2 authorization code (short-lived, single-use)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_authorization_codes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped["OAuthClient"] = relationship("OAuthClient", back_populates="auth_codes")  # noqa: F821
