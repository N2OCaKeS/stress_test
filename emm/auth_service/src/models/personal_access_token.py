"""ORM-модель `PersonalAccessToken` — PAT юзера (raw показан один раз, в БД hash + префикс)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"
    # Partial unique: имя занято только пока PAT активен. Revoke освобождает
    # имя для повторной выписки (rotation flow). Совпадает с фильтром
    # `exists_name` на code-уровне.
    __table_args__ = (
        Index(
            "uq_pat_user_name_active",
            "user_id",
            "name",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    # Первые 12 символов raw-токена — для распознавания юзером, НЕ секрет
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # Подмножество allowed_services юзера; не может превышать его собственные права
    allowed_services: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Причина revoke'а: "ban" / "user" / "expired" / "admin_reset" / NULL legacy.
    # `unban_user` смотрит "ban", чтобы знать какие PAT'ы реактивировать.
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="personal_access_tokens")  # noqa: F821
