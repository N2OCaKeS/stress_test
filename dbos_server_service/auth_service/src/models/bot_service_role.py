"""ORM-модель `BotServiceRole` — назначение service-роли боту.

Зеркало `UserServiceRole` для bot accounts. Каталог ролей по-прежнему scope-
аится по `(bot.department_id, service_name)` — здесь только сам binding.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class BotServiceRole(Base):
    __tablename__ = "bot_service_roles"
    __table_args__ = (
        UniqueConstraint("bot_id", "service_name", "role", name="uq_bot_service_role"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("bot_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_name: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("platform_services.service_name", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    assigned_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    bot: Mapped["BotAccount"] = relationship("BotAccount", back_populates="service_roles")  # noqa: F821
