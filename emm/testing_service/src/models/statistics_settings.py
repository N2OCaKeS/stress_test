"""Настройки доступа к внешнему сервису статистики (§2.7, §9.3 плана миграции).

Сервис статистики (ветка `statistics` этого же монорепо, `statistics/main_api.py`)
живёт отдельно от testing_service, один инстанс на всю платформу — не
per-department, поэтому одна singleton-строка (по образцу `AcsSettings` в
server_service), а не таблица по `department_id`. `enabled=False` по
умолчанию — фоновый пересчёт выключен, пока роль admin явно не включит его и
не укажет `base_url`.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Единственная допустимая строка таблицы. Все чтения/записи идут по этому PK.
SINGLETON_ID = "default"


class StatisticsSettings(Base):
    """Платформенный singleton-конфиг доступа к внешнему сервису статистики."""

    __tablename__ = "statistics_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
