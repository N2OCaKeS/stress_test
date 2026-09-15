"""Ячейка СТП — статус `(stp_test_case × stp_test_run)` (§2.5, §6.2, §D4/D5 плана миграции).

Ровно одно из `queue_item_id`/`updated_by` заполнено (constraint на уровне
приложения, см. `services/stp_status.py` — событийный путь пишет
`queue_item_id`, ручной override пишет `updated_by`, никогда оба сразу).
`UNIQUE(stp_test_case_id, stp_test_run_id)` — одна ячейка на пару, создаётся
один раз (при первой генерации СТП либо при последующем расширении состава,
см. `services/stp.py`), дальше только обновляется.

`is_active` (§D5) — входит ли ячейка в ТЕКУЩИЙ активный состав (`stp_
compositions.scope`). Переключение changelog/full не удаляет и не сбрасывает
ячейку — тесты, выпавшие из объёма, получают `is_active=False`, сохраняя
`status`/`queue_item_id`/`updated_by`; обратное переключение просто
возвращает `is_active=True` тем же строкам.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.constants import StpCellStatus
from src.db.base import Base


class StpCell(Base):
    """Одна ячейка таблицы СТП — статус конкретного тест-кейса в конкретном прогоне."""

    __tablename__ = "stp_cells"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stp_test_case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stp_test_cases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    stp_test_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stp_test_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=StpCellStatus.NOT_RUN,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Заполнен, когда статус пришёл автоматически из queue_items (§6.2).
    queue_item_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("queue_items.id", ondelete="SET NULL"), nullable=True,
    )
    # Заполнен, когда статус выставлен вручную (PATCH /stp/cells/{id}).
    # Soft-FK на auth_service identity — не сюда, а на пользователя, поставившего override.
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        UniqueConstraint("stp_test_case_id", "stp_test_run_id", name="uq_stp_cells_case_run"),
    )
