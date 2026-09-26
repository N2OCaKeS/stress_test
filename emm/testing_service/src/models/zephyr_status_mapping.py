"""Маппинг статусов Zephyr → исход теста.

Вердикт теста — статус, который **сам скрипт** выставил тест-кейсу в
прогоне Zephyr (`allta_app_full/libs/zefir.py:54-58`: 91 «Выполнено»,
92 «Провалено», 90 «Выполняется», 89 «Не запускался»). Эта таблица говорит,
какой статус что значит: `passed` / `failed` / `not_finished` (ждать
дальше).

`department_id IS NULL` — набор по умолчанию (сид миграции
`tp07_zephyr_verdict`). Если у отдела есть хотя бы одна своя строка, он
использует только свои строки; «Сбросить к умолчанию» удаляет их.

`zephyr_status` сравнивается без учёта регистра и пробелов по краям: ATM
REST (`GET /rest/atm/1.0/testrun/{key}`) отдаёт имя статуса («Pass»),
внутренний `/rest/tests/1.0/` легаси — числовой id («91»); в сиде есть оба.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ZephyrStatusMapping(Base):
    """Одна строка: статус Zephyr → `passed`/`failed`/`not_finished`."""

    __tablename__ = "zephyr_status_mappings"
    __table_args__ = (
        UniqueConstraint(
            "department_id", "zephyr_status",
            name="uq_zephyr_status_mappings_dept_status",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "outcome IN ('passed', 'failed', 'not_finished')",
            name="ck_zephyr_status_mappings_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    zephyr_status: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
