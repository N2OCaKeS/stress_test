"""СТП-прогон — Zephyr test-run/execution (§2.5, §6.1 плана миграции).

НЕ то же самое, что `test_runs` (§2.4, fleet-wide кампания постановки в
очередь) — `stp_test_runs` это созданный в Zephyr Scale тест-ран (один на
стенд на вызов `/stp/generate`), несущий набор тест-кейсов (`stp_cells`),
чей статус отражается сюда событийно из очереди (§6.2).

`stand_id` — настоящий FK на `test_stands` (в этой же БД), `RESTRICT`: пока
существует историческая ссылка СТП-прогона на стенд, стенд не удаляется.
`os_version_id`/`mode`/`kernel` — снэпшот параметров генерации, без FK — тот
же приём, что у `test_runs`/`global_variable`.

Департамент здесь намеренно не хранится (см. §2.5 плана миграции) — при
событийном обновлении статуса (`services/stp_status.py`) department
резолвится через `test_stands.department_id` по `stand_id`.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class StpTestRun(Base):
    """Один Zephyr test-run, созданный `/stp/generate` для одного стенда."""

    __tablename__ = "stp_test_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    os_version_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    kernel: Mapped[str] = mapped_column(String(64), nullable=False)
    stand_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_stands.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    # Заполняется после успешного создания в Zephyr; NULL — генерация ещё не
    # дошла до Zephyr-вызова или он провалился (см. частичные ошибки §5).
    zephyr_test_run_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    zephyr_folder_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
