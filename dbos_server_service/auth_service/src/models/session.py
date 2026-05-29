"""ORM-модель `Session` — refresh-токен + история previous_token_hashes для reuse-detection (kill-switch)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

# Размер sliding-window для reuse-detection. Reuse refresh'а, ротированного
# больше PREVIOUS_TOKEN_HASH_WINDOW поколений назад, уже не отловим — это
# trade-off между объёмом строки и глубиной защиты.
PREVIOUS_TOKEN_HASH_WINDOW = 5


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    # Legacy: один предыдущий hash. Оставлен для совместимости со старыми
    # строками и миграционным даунгрейдом; новый код пишет в
    # `previous_token_hashes` и оттуда же читает.
    previous_token_hash: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    # Последние PREVIOUS_TOKEN_HASH_WINDOW hash'ей в порядке от старого к
    # новому. На каждой ротации старый refresh_token_hash аппендится в хвост,
    # голова обрезается. Reuse-detection ищет среди элементов массива.
    previous_token_hashes: Mapped[list[str]] = mapped_column(
        ARRAY(String(256)), nullable=False, default=list
    )
    # Инкрементится на каждом успешном refresh; используется для reuse-detection
    token_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Поднимается, если уже ротированный refresh подсунули ещё раз — kill-switch
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="sessions")  # noqa: F821
