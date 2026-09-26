"""Verdict from Zephyr (status mappings, awaiting_verdict, settings)

Revision ID: b8e4f1a7c265
Revises: d5a8e2c47b19
Create Date: 2026-09-24 00:00:00.000000

Исход теста (решения D1, T3, D13 плана паритета) — статус, который
скрипт сам выставляет тест-кейсу в прогоне Zephyr, а не код выхода
`starter.sh` (`run.py` всех веток выходит с 0).

* `zephyr_status_mappings` — статус Zephyr → `passed`/`failed`/
  `not_finished`. Сид — набор по умолчанию (`department_id IS NULL`):
  - имена статусов ATM REST — прежний хардкод
    `services/zephyr_client.py::_STATUS_MAP` («Pass», «Fail»,
    «In Progress», «Not Executed»);
  - числовые id внутреннего API легаси —
    `emm/allta_app_full/libs/zefir.py:54-58` (92 «Провалено», 91
    «Выполнено», 90 «Выполняется», 89 «Не запускался»); `pass`/`fail`
    скрипта → 91/92 — `emm/allta_app_full/backup_image.py:502-505`.
* `department_test_settings`: ждать итогового статуса 35 минут, опрос раз в
  60 с (решение T3 от 24.09: скрипты публикуют асинхронно — легаси
  `emm/allta_app_full/backup_image.py:516-530` повторяет раз в 60 с, пока
  Jira/Confluence не ответят 200); не дождались — `failed`
  (T3); запуск без прогона в Zephyr — вердикт `unknown`.
* `test_definitions.verdict_source` = `zephyr` — легаси-поведение для всех
  существующих тестов.
* `queue_items`: `verdict_source`, `verdict`, `zephyr_status_raw`,
  `verdict_wait_started_at`, `zephyr_polled_at`, `verdict_resolved_at`
  (C6). Состояние `awaiting_verdict` (C5) — только значение
  `queue_items.state` (String(16), без CHECK), DDL не нужен.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4f1a7c265"
down_revision: Union[str, None] = "d5a8e2c47b19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEFAULT_MAPPINGS: tuple[tuple[str, str], ...] = (
    ("Pass", "passed"),
    ("Fail", "failed"),
    ("In Progress", "not_finished"),
    ("Not Executed", "not_finished"),
    ("91", "passed"),
    ("92", "failed"),
    ("90", "not_finished"),
    ("89", "not_finished"),
)


def upgrade() -> None:
    op.create_table(
        "zephyr_status_mappings",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("zephyr_status", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "department_id", "zephyr_status",
            name="uq_zephyr_status_mappings_dept_status",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'not_finished')",
            name="ck_zephyr_status_mappings_outcome",
        ),
    )
    op.create_index(
        "ix_zephyr_status_mappings_department_id", "zephyr_status_mappings", ["department_id"],
    )
    mappings = sa.table(
        "zephyr_status_mappings",
        sa.column("id", sa.String), sa.column("department_id", sa.String),
        sa.column("zephyr_status", sa.String), sa.column("outcome", sa.String),
    )
    op.bulk_insert(mappings, [
        {"id": f"zsm_{uuid4().hex}", "department_id": None, "zephyr_status": status, "outcome": outcome}
        for status, outcome in _DEFAULT_MAPPINGS
    ])

    op.add_column("department_test_settings", sa.Column(
        "zephyr_verdict_wait_seconds", sa.Integer(), nullable=False, server_default="2100",
    ))
    op.add_column("department_test_settings", sa.Column(
        "zephyr_verdict_poll_seconds", sa.Integer(), nullable=False, server_default="60",
    ))
    op.add_column("department_test_settings", sa.Column(
        "zephyr_verdict_unfinished_outcome", sa.String(length=16), nullable=False, server_default="failed",
    ))
    op.add_column("department_test_settings", sa.Column(
        "verdict_without_zephyr_run", sa.String(length=16), nullable=False, server_default="unknown",
    ))

    op.add_column("test_definitions", sa.Column(
        "verdict_source", sa.String(length=16), nullable=False, server_default="zephyr",
    ))
    op.create_check_constraint(
        "ck_test_definitions_verdict_source", "test_definitions",
        "verdict_source IN ('zephyr', 'exit_code')",
    )

    op.add_column("queue_items", sa.Column("verdict_source", sa.String(length=16), nullable=True))
    op.add_column("queue_items", sa.Column("verdict", sa.String(length=16), nullable=True))
    op.add_column("queue_items", sa.Column("zephyr_status_raw", sa.String(length=64), nullable=True))
    op.add_column("queue_items", sa.Column("verdict_wait_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("queue_items", sa.Column("zephyr_polled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("queue_items", sa.Column("verdict_resolved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # `awaiting_verdict` без колонок ожидания не доживёт — такие item'ы
    # проваливаем, чтобы стенд не остался занятым навсегда.
    op.execute(
        "UPDATE queue_items SET state = 'failed', finished_at = now(), "
        "error = 'awaiting_verdict dropped by downgrade' WHERE state = 'awaiting_verdict'"
    )
    for column in (
        "verdict_resolved_at", "zephyr_polled_at", "verdict_wait_started_at",
        "zephyr_status_raw", "verdict", "verdict_source",
    ):
        op.drop_column("queue_items", column)
    op.drop_constraint("ck_test_definitions_verdict_source", "test_definitions", type_="check")
    op.drop_column("test_definitions", "verdict_source")
    for column in (
        "verdict_without_zephyr_run", "zephyr_verdict_unfinished_outcome",
        "zephyr_verdict_poll_seconds", "zephyr_verdict_wait_seconds",
    ):
        op.drop_column("department_test_settings", column)
    op.drop_index("ix_zephyr_status_mappings_department_id", table_name="zephyr_status_mappings")
    op.drop_table("zephyr_status_mappings")
