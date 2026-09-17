"""Настройки тестирования отдела (§2.4 плана миграции).

Одна строка на `department_id`. Отсутствие строки — штатный случай, а не
ошибка: сервис отдаёт дефолты (см. `services/department_test_settings.py`),
строка появляется только при первом `PUT`.

`activity_report_auto_generate` — включает фоновую ежемесячную генерацию
HR-отчёта за предыдущий месяц (см. `services/activity_report.py::run_auto_generate_tick`,
цикл в `src/main.py`).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class DepartmentTestSettings(Base):
    """Per-department переключатели retry/имени тестового пользователя."""

    __tablename__ = "department_test_settings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    retry_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    test_username: Mapped[str] = mapped_column(String(32), nullable=False, default="u")
    activity_report_auto_generate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
