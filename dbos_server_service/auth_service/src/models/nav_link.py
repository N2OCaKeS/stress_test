"""ORM-модель `NavLink` — настраиваемая кнопка левой панели web-UI.

Одна строка на всю платформу (fixed PK `"default"`). account_admin задаёт
подпись, внешний URL, флаг видимости и список отделов, которым кнопка видна.
Отсутствие строки или `enabled=False` = кнопка не показывается никому.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Sentinel PK единственной строки: upsert всегда метит этот id, так что
# больше одной строки в таблице завестись не может.
SINGLETON_ID = "default"

DEFAULT_LABEL = "allta"


class NavLink(Base):
    __tablename__ = "nav_link"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    label: Mapped[str] = mapped_column(String(64), default=DEFAULT_LABEL, nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    all_departments: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    department_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
