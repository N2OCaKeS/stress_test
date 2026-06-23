"""ORM-модель `LockoutPolicy` — runtime-override параметров brute-force lockout'а.

Одна строка на всю платформу (fixed PK `"default"`). Отсутствие строки = тянем
env-дефолты из `core.config` (`max_failed_login_attempts` / `lockout_minutes`).
Резолв эффективных значений — `services.lockout_policy_service.resolve_lockout_policy`.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Sentinel PK единственной строки: upsert всегда метит этот id, так что
# больше одной строки в таблице завестись не может.
SINGLETON_ID = "default"


class LockoutPolicy(Base):
    __tablename__ = "lockout_policy"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    max_failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lockout_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
