"""Многостендовый сценарий (решение D12.2): описание без исполнения.

Сценарий — упорядоченные действия над несколькими стендами пула: прогнать
тест на стенде, подготовить стенд, подождать. Легаси-пример — FreeIPA
(`emm/allta_app_full/backup_image.py:943-964`): КД на физическом стенде,
клиент на ВМ.

Ролей нет (решение владельца 24.09): сценарий ссылается на конкретные
стенды пула (`test_stands`); цель — сервер или ВМ — берётся из самого стенда
(`test_stands.target_type`). Чтобы запустить на других стендах,
владелец правит сценарий.

Бронь, подготовку и исполнение делает `scenario_queue.py`. Вердикт сценария — по
действиям `run_test` с `is_verdict`.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        CheckConstraint("readiness IN ('ready', 'review', 'broken', 'development')", name="ck_scenarios_readiness"),
        UniqueConstraint("department_id", "stp_test_case_code", name="uq_scenarios_department_stp_case"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    readiness: Mapped[str] = mapped_column(
        String(32), nullable=False, default="development", server_default="development",
    )
    # Тест-кейс СТП, который запускается этим сценарием: кампания по СТП
    # ставит вместо одиночного теста запуск сценария (если он `ready`), а
    # вердикт пишется в ячейку этого кейса.
    stp_test_case_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class ScenarioStand(Base):
    """Стенд сценария и как его готовить перед сценарием."""

    __tablename__ = "scenario_stands"
    __table_args__ = (
        UniqueConstraint("scenario_id", "stand_id", name="uq_scenario_stands_stand"),
        CheckConstraint("preparation IN ('full', 'revert_only', 'none')", name="ck_scenario_stands_preparation"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # Стенд пула (`test_stands.id`); сырой id, как `pinned_stand_id` теста.
    # Удаление стенда, на который ссылается сценарий, — 409.
    stand_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # `full` — restore + подготовка; `revert_only` — только откат; `none`.
    preparation: Mapped[str] = mapped_column(String(16), nullable=False, default="full", server_default="full")
    # Без PAM-правки при подготовке, что бы ни говорил профиль; от `preparation` не зависит.
    skip_pam_fix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    provisioning_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Шаг настройки стенда, формат (как `test_definitions.stand_setup`).
    stand_setup: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    kernel_override: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mode_override: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ScenarioAction(Base):
    """Шаг сценария: `run_test` | `prepare_stand` | `wait`."""

    __tablename__ = "scenario_actions"
    __table_args__ = (
        UniqueConstraint("scenario_id", "position", name="uq_scenario_actions_position"),
        CheckConstraint("kind IN ('run_test', 'prepare_stand', 'wait')", name="ck_scenario_actions_kind"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scenario_stand_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("scenario_stands.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    test_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_verdict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # `wait` — `{"seconds": N}`; прочим — пусто.
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class ScenarioRun(Base):
    """Запуск сценария: контекст запуска, состояние, вердикт.

    Действия исполняются обычными `queue_items` с `scenario_run_id` —
    логи, вердикт, skip работают как у одиночного item'а.
    """

    __tablename__ = "scenario_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('waiting_for_stands', 'preparing', 'running', 'stopping', 'succeeded', 'failed', 'stopped')",
            name="ck_scenario_runs_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # RC / KERNEL / MODE запуска; ядро и режим стенда — override стенда сценария.
    launch_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    debug_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Запуск из кампании (`test_runs`) и столбец СТП, куда пишется вердикт.
    test_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    test_run_entry_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("test_run_entries.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    stp_test_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("stp_test_runs.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # Позиция текущего действия (NULL — действия ещё не начинались).
    current_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_queue_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Действие `wait`: когда идти дальше (двигает фоновый тик).
    wait_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Занятые стенды при `waiting_for_stands`: `[{stand_id, reason}]`.
    blocked_by: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class ScenarioRunStand(Base):
    """Снимок стенда сценария на момент запуска и его подготовка."""

    __tablename__ = "scenario_run_stands"
    __table_args__ = (
        UniqueConstraint("scenario_run_id", "stand_id", name="uq_scenario_run_stands_stand"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("scenario_runs.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    scenario_stand_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stand_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preparation: Mapped[str] = mapped_column(String(16), nullable=False)
    skip_pam_fix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    kernel: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    provisioning_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stand_setup: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    prepare_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(String(2048), nullable=True)

