"""История генераций HR-отчёта по активности отдела (§2.7, §9.1 плана миграции).

Одна строка — одна попытка генерации отчёта за `period` (`"YYYY-MM"`).
Легаси перезапускал скрипт вручную с новым `MONTH` в коде и не хранил историю
запусков вообще — здесь `POST .../activity-reports/generate` каждый раз
заводит новую строку (повторная генерация того же периода — новая попытка,
не upsert: полезно видеть историю, если публикация упала и её повторили).
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class DepartmentActivityReport(Base):
    """Одна попытка генерации HR-отчёта отдела за период."""

    __tablename__ = "department_activity_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "YYYY-MM"
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Soft-FK на auth_service identity — кто запустил генерацию.
    generated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confluence_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
