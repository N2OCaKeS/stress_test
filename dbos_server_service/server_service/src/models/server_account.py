"""Модель ServerAccount — локальный OS-аккаунт с зашифрованным паролем."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class ServerAccount(Base):
    """OS-аккаунт на сервере. Пароль хранится в формате secrets_service token."""

    __tablename__ = "server_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    login: Mapped[str] = mapped_column(String(128), nullable=False)
    # Формат: `v<key>$<nonce>$<ciphertext>` (см. secrets_service.py).
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_sudo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unix_groups: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    linked_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    shell: Mapped[str | None] = mapped_column(String(64), nullable=True)
    home_dir: Mapped[str | None] = mapped_column(String(256), nullable=True)
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

    __table_args__ = (
        # Один логин на один сервер — нельзя завести двух root'ов.
        UniqueConstraint("server_id", "login", name="uq_server_account_login"),
    )

    server: Mapped["Server"] = relationship(  # noqa: F821
        "Server", back_populates="accounts"
    )
