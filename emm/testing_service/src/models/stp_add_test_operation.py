"""Долговечная операция «добавить один тест EMM в СТП» (§D6/D7 плана миграции).

Узкий аналог `services/stp.py::generate_stp_runs` для случая, когда QA-лид
добавляет ОДИН тест в уже существующий Zephyr test-run вручную, а не
переключает состав отдела/РЦ целиком. Четыре шага выполняются по порядку:

1. `zephyr_testcase_created` — в Zephyr заведён (или переиспользован
   существующий) testcase, `stp_test_case_id` указывает на локальную
   карточку `stp_test_cases`.
2. `zephyr_added_to_run` — testcase добавлен в Zephyr test-run
   (`stp_test_runs.zephyr_test_run_key`), либо уже был там.
3. `stp_cell_created` — локальная ячейка `stp_cells` заведена,
   `stp_cell_id` заполнен.
4. `life_published` — СТП-матрица (Confluence/life) republished — либо
   реально ушла публикация, либо `stp_matrix.publish_stp_matrix` вернул
   легитимный skip (не настроено/нет прогонов), что тоже терминально для
   этого шага, в отличие от `failed`.

Ровно одна строка на пару `(test_definition_id, stp_test_run_id)` —
повторный вызов на ту же пару находит существующую операцию и продолжает с
первого не пройденного шага, а не плодит дубли ни локально, ни в Zephyr.
`status`: `pending` пока не все шаги пройдены, `succeeded` — все четыре,
`failed` — шаг провалился (уже пройденные шаги/`last_error` сохраняются для
диагностики и последующего повтора).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.constants import StpAddTestOperationStatus
from src.db.base import Base


class StpAddTestOperation(Base):
    """Одна операция добавления теста в конкретный СТП test-run."""

    __tablename__ = "stp_add_test_operations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Отдел, чьи Jira/Confluence-настройки резолвятся для шагов 1/2/4 —
    # снимается со стенда прогона (`test_stands.department_id`) в момент
    # заведения операции, не с caller'а (тот может быть department_admin
    # чужого отдела через bypass — операция должна остаться привязанной
    # к отделу самого прогона).
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    test_definition_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_definitions.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    stp_test_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stp_test_runs.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    # Заполняется по завершении шага 1 — какая карточка `stp_test_cases`
    # использована (найдена существующая либо заведена новая).
    stp_test_case_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("stp_test_cases.id", ondelete="SET NULL"), nullable=True,
    )
    # Заполняется по завершении шага 3.
    stp_cell_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("stp_cells.id", ondelete="SET NULL"), nullable=True,
    )
    zephyr_testcase_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    zephyr_added_to_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    stp_cell_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    life_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False,
        default=StpAddTestOperationStatus.PENDING, server_default=StpAddTestOperationStatus.PENDING,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-FK на auth_service identity — кто инициировал добавление.
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
        UniqueConstraint("test_definition_id", "stp_test_run_id", name="uq_stp_add_test_op_test_run"),
    )
