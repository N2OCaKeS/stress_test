"""activity report auto-generate toggle

Revision ID: 2bf709abc3d2
Revises: be38ca3bdffb
Create Date: 2026-09-17 00:00:00.000000

Заменяет `department_test_settings.activity_report_schedule` (свободный
текст, ничем не читался) на `activity_report_auto_generate` (boolean) — фоновая
ежемесячная генерация HR-отчёта за предыдущий месяц, включаемая простым
переключателем (см. `services/activity_report.py::run_auto_generate_tick`,
цикл в `src/main.py`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "2bf709abc3d2"
down_revision: Union[str, None] = "be38ca3bdffb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("department_test_settings", "activity_report_schedule")
    op.add_column(
        "department_test_settings",
        sa.Column(
            "activity_report_auto_generate", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("department_test_settings", "activity_report_auto_generate")
    op.add_column(
        "department_test_settings",
        sa.Column("activity_report_schedule", sa.String(length=128), nullable=True),
    )
