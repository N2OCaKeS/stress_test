"""`RetentionPolicy` — срок хранения событий, опционально per severity / service.

`severity` и `service` (миграция `g7b8c9d0e1f2`) — каждый row пишет одну
комбинацию `(severity_i, service_j)` или NULL/NULL для глобальной
политики. Cartesian expansion — на уровне `repositories::create_policy`
(см. там).
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.utils.ids import _new_id


def _retention_policy_id() -> str:
    return _new_id("rp_")


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=_retention_policy_id)

    # NULL = применяется ко всем severity / всем сервисам.
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    service: Mapped[str | None] = mapped_column(String(64), nullable=True)

    retain_days: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
