"""Шаг теста — одна фаза многоступенчатого теста.

Тест исполняется шагами по порядку `position` на одной брони стенда.
Одношаговый тест — частный случай: у него ровно один шаг. У шага:

* свои слоты команды (`test_command_args.step_id`) — содержимое `dates.conf`
  этой фазы;
* `starter_suffix` — позиционный `$5` у `starter.sh` (`kernel`/`balance`/
  `oom` или пусто), флаг `run.py` клонированной ветки;
* `run_mode` (`core.constants.StepRunMode`): `full` — команда запуска
  профиля (`starter.sh` клонирует ветку и готовит стенд); `rerun` —
  повторный запуск уже склонированного кода (`rerun_script` профиля
  запуска кладётся вместо `starter.sh`);
* `stand_setup` — шаг настройки стенда (, форма —
  `schemas/test_definition.py::StandSetup`). У первого шага он уходит в
  `prepare-for-test`, у остальных — отдельной операцией «настройка без
  restore» перед шагом (`services/queue_steps.py`). NULL — настройки нет.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestStep(Base):
    """Одна фаза теста."""

    __tablename__ = "test_steps"
    __table_args__ = (
        CheckConstraint("run_mode IN ('full', 'rerun')", name="ck_test_steps_run_mode"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    test_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_definitions.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # Порядок шагов; как у слотов, уникальность не форсируется БД —
    # перестановка идёт одним PUT со всем порядком (`services/test_step.py`).
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    starter_suffix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="full", server_default="full")
    stand_setup: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
