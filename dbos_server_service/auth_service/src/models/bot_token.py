"""ORM-модель `BotToken` — opaque-токен бота (raw показывается один раз, в БД hash + префикс)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class BotToken(Base):
    __tablename__ = "bot_tokens"
    # Partial unique: имя занято только у активных токенов. После revoke
    # dept_admin может выпустить новый токен с тем же именем (ротация раз
    # в полгода). Code-уровень `exists_name` фильтрует так же.
    __table_args__ = (
        Index(
            "uq_bot_token_name_active",
            "bot_id",
            "name",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("bot_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bot: Mapped["BotAccount"] = relationship("BotAccount", back_populates="tokens")  # noqa: F821
