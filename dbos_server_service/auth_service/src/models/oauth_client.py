"""ORM-модель `OAuthClient` — per-department application credentials для OAuth2."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    client_secret_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    client_secret_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    department_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    allowed_scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    # Поддерживаемые grant types: authorization_code, client_credentials
    grant_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
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

    department: Mapped["Department"] = relationship("Department", back_populates="oauth_clients")  # noqa: F821
    auth_codes: Mapped[list["OAuthAuthorizationCode"]] = relationship(  # noqa: F821
        "OAuthAuthorizationCode", back_populates="client", cascade="all, delete-orphan"
    )
