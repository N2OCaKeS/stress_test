"""ORM-модель `BotGroupMembership` — связь «бот состоит в группе».

Отдельная таблица от `user_group_memberships` (без полиморфизма): бот — самостоятельная
сущность отдела, но роли наследует из тех же `user_groups`, что и юзеры.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class BotGroupMembership(Base):
    __tablename__ = "bot_group_memberships"
    __table_args__ = (
        UniqueConstraint("group_id", "bot_id", name="uq_bot_group_membership"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("bot_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    added_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    group: Mapped["UserGroup"] = relationship("UserGroup", back_populates="bot_memberships")  # noqa: F821
    bot: Mapped["BotAccount"] = relationship("BotAccount", back_populates="group_memberships")  # noqa: F821
