"""Публикация СТП-матрицы в Confluence (§D2/D3 плана миграции — эталонные отчёты).

Перенос легаси `ZefirResultTable` (`allta_app/libs/zefir.py:203-436`): сводная
HTML-таблица (версия/ядро/режим/стенд × тест-кейс → статус) с цветовой
подсветкой, публикуется как страница Confluence в трёхуровневой иерархии
(grandparent → `STRESS_stp ⬝ {release}` → сама страница РЦ). В отличие от
легаси — пространство и заголовок корневой страницы настраиваются per-
department (`department_integration_settings.stp_matrix_confluence_*`), не
хардкод `DEVQA`/`'Состав тестового прогона'`.

Одна строка на пару `(department_id, os_version_id)` — повторная публикация
того же РЦ того же отдела обновляет существующую страницу (find-or-update),
не заводит дубль. `body_snapshot` — тот же приём, что у `run_summary_comment`
и `department_activity_report`: снимок последнего отправленного HTML, чтобы
не дёргать Confluence API, если таблица не изменилась с прошлой публикации.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class StpMatrixPublication(Base):
    """Состояние публикации сводной СТП-таблицы одного РЦ одного отдела."""

    __tablename__ = "stp_matrix_publications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    os_version_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    # Id страницы РЦ и её непосредственного родителя (`STRESS_stp ⬝ {release}`)
    # — оба найдены-или-заведены нами, в отличие от run_summary/activity_report,
    # где родительская страница остаётся внешним предусловием.
    confluence_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confluence_parent_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Последний отправленный HTML — basis для diff перед update_page.
    body_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Soft-FK на auth_service identity — кто запустил публикацию последний раз.
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        UniqueConstraint("department_id", "os_version_id", name="uq_stp_matrix_publications_dept_osv"),
    )
