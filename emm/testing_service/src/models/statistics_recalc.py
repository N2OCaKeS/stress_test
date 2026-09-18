"""Индикатор фонового пересчёта статистики (§2.7, §9.3 плана миграции).

Одна платформенная строка — "что происходит сейчас / чем закончилась
последняя попытка", а не журнал: сам внешний сервис статистики один на всю
платформу и не параллелит свои семейства тестов внутри одного вызова
`/all-statistics`, поэтому осмысленно отслеживать только одну попытку сразу.
Обновляется `services/statistics_recalc.py` до/после фонового HTTP-вызова.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

SINGLETON_ID = "default"


class StatisticsRecalcState(Base):
    """Платформенный singleton — статус последнего/текущего пересчёта статистики."""

    __tablename__ = "statistics_recalc_status"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle", server_default="idle")
    # "test_run" (автоматически на терминальном статусе кампании) или "manual"
    # (кнопка/переключатель в UI для одиночных тестов).
    triggered_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Какое семейство тестов пересчитывалось (`services/statistics_client.
    # CATEGORIES`). NULL — полный пересчёт `/all-statistics`; автотриггер по
    # кампании всегда такой, категорию задаёт только ручная кнопка.
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Заполнено только для triggered_by="test_run" — какая кампания вызвала
    # этот конкретный пересчёт. Без FK — чисто информационная ссылка.
    test_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
