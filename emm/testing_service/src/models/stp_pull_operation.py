"""Одна попытка затянуть Zephyr test-run из life в EMM (§D8 плана миграции).

`services/stp_pull_from_life.py` читает Zephyr (папку `/stress_test/{release}/
{os_version_id}`), парсит имя найденного test-run'а той же схемой, что и
`services/stp.py::_create_stand_run` использует для генерации, и заводит/
обновляет локальные `stp_test_run`/`stp_test_case`/`stp_cell`. Эта таблица —
не шаговый чек-лист вроде `StpAddTestOperation` (тут нет записи ОБРАТНО в
Zephyr — импорт не публикует ничего в life), а просто память последней
попытки на конкретный `zephyr_test_run_key`: что нашли, что завели, что
конфликтует. Нужна, чтобы повторный вызов на тот же ключ мог отчитаться, что
изменилось с прошлого раза, и чтобы сетевой сбой на одном ране не потерялся
молча среди остальных найденных в папке.

`result_summary` — JSON-снимок счётчиков последней попытки (`created_run`,
`matched_run`, `cases_created`, `cases_matched`, `cells_created`,
`cells_matched`, `conflicts`) — тот же приём, что `body_snapshot` у
`StpMatrixPublication`.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.constants import StpPullOperationStatus
from src.db.base import Base


class StpPullOperation(Base):
    """Одна запись о попытке импорта конкретного Zephyr test-run'а из life."""

    __tablename__ = "stp_pull_operations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    os_version_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    zephyr_test_run_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Заполняется, как только локальный прогон найден/заведён.
    stp_test_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("stp_test_runs.id", ondelete="SET NULL"), nullable=True,
    )
    run_upserted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    cells_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False,
        default=StpPullOperationStatus.PENDING, server_default=StpPullOperationStatus.PENDING,
    )
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-FK на auth_service identity — кто запустил импорт.
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "department_id", "os_version_id", "zephyr_test_run_key",
            name="uq_stp_pull_op_dept_os_key",
        ),
    )
