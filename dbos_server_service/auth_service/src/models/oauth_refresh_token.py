"""ORM-модель `OAuthRefreshToken` — ротируемый OAuth2 refresh с reuse-detection.

Зеркалит `Session` (user-сессионный refresh): opaque-токен, в БД только hash,
CAS-замена при ротации, sliding-window истории previous-hash'ей для отлова
reuse ротированного токена → kill-switch по всей цепочке (client_id, user_id).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

# Глубина sliding-window для reuse-detection (как у `Session`): reuse refresh'а,
# ротированного больше N поколений назад, уже не отловим — trade-off между
# объёмом строки и глубиной защиты.
PREVIOUS_TOKEN_HASH_WINDOW = 5


class OAuthRefreshToken(Base):
    __tablename__ = "oauth_refresh_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Привязка к клиенту: цепочка ротаций принадлежит одной паре
    # (client_id, user_id). reuse бьёт только по этой цепочке.
    client_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    # Последние PREVIOUS_TOKEN_HASH_WINDOW hash'ей в порядке от старого к новому.
    # На каждой ротации текущий hash аппендится в хвост, голова обрезается.
    # Reuse-detection ищет среди элементов массива.
    previous_token_hashes: Mapped[list[str]] = mapped_column(
        ARRAY(String(256)), nullable=False, default=list
    )
    # Снапшот approved scope'ов: новый access несёт INTERSECT(scopes, ...) как
    # на authorization_code-обмене — refresh не может расширить выданные права.
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    # Инкрементится на каждой успешной ротации; для reuse-detection и аудита.
    token_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Поднимается, если ротированный refresh подсунули ещё раз — kill-switch.
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped["OAuthClient"] = relationship("OAuthClient")  # noqa: F821
