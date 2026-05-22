"""ORM-модель `BotAccount` — service-account внутри отдела.

`allowed_services` ограничивает, к каким сервисам бот может получать токены.
`created_by` хранит user_id создавшего — используется при ban'е юзера для
каскадного revoke всех bot-токенов owned-ботов (см. `user_service.ban_user`).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.constants import BotStatus
from src.db.base import Base


class BotAccount(Base):
    __tablename__ = "bot_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    department_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    allowed_services: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), default=BotStatus.ACTIVE, nullable=False)
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

    department: Mapped["Department"] = relationship("Department", back_populates="bots")  # noqa: F821
    tokens: Mapped[list["BotToken"]] = relationship(  # noqa: F821
        "BotToken", back_populates="bot", cascade="all, delete-orphan"
    )
    service_roles: Mapped[list["BotServiceRole"]] = relationship(  # noqa: F821
        "BotServiceRole", back_populates="bot", cascade="all, delete-orphan"
    )
