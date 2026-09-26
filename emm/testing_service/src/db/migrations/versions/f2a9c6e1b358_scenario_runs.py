"""Scenario runs (scenario_runs, scenario_run_stands, queue_items.scenario_run_id)

Revision ID: f2a9c6e1b358
Revises: e8c4b1f7a3d5
Create Date: 2026-09-24 23:00:00.000000

Запуск многостендового сценария. Бронь всех стендов
«всё или ничего», параллельная подготовка, действия — обычные `queue_items`
с `scenario_run_id`/`scenario_action_id`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a9c6e1b358"
down_revision: Union[str, None] = "e8c4b1f7a3d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scenario_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "scenario_id", sa.String(64), sa.ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("department_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("launch_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("debug_mode", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("test_run_id", sa.String(64), nullable=True),
        sa.Column("current_position", sa.Integer(), nullable=True),
        sa.Column("current_queue_item_id", sa.String(64), nullable=True),
        sa.Column("wait_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_by", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("verdict", sa.String(16), nullable=True),
        sa.Column("error", sa.String(2048), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('waiting_for_stands', 'preparing', 'running', 'stopping', 'succeeded', 'failed', 'stopped')",
            name="ck_scenario_runs_state",
        ),
    )
    op.create_index("ix_scenario_runs_scenario_id", "scenario_runs", ["scenario_id"])
    op.create_index("ix_scenario_runs_department_id", "scenario_runs", ["department_id"])
    op.create_index("ix_scenario_runs_state", "scenario_runs", ["state"])

    op.create_table(
        "scenario_run_stands",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "scenario_run_id", sa.String(64), sa.ForeignKey("scenario_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scenario_stand_id", sa.String(64), nullable=False),
        sa.Column("stand_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("preparation", sa.String(16), nullable=False),
        sa.Column("kernel", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("provisioning_profile_id", sa.String(64), nullable=True),
        sa.Column("stand_setup", postgresql.JSONB(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("prepare_request_id", sa.String(64), nullable=True),
        sa.Column("error", sa.String(2048), nullable=True),
        sa.UniqueConstraint("scenario_run_id", "stand_id", name="uq_scenario_run_stands_stand"),
    )
    op.create_index("ix_scenario_run_stands_scenario_run_id", "scenario_run_stands", ["scenario_run_id"])
    op.create_index("ix_scenario_run_stands_stand_id", "scenario_run_stands", ["stand_id"])
    op.create_index("ix_scenario_run_stands_state", "scenario_run_stands", ["state"])
    op.create_index("ix_scenario_run_stands_prepare_request_id", "scenario_run_stands", ["prepare_request_id"])

    op.add_column("queue_items", sa.Column("scenario_run_id", sa.String(64), nullable=True))
    op.add_column("queue_items", sa.Column("scenario_action_id", sa.String(64), nullable=True))
    op.create_index("ix_queue_items_scenario_run_id", "queue_items", ["scenario_run_id"])


def downgrade() -> None:
    op.drop_index("ix_queue_items_scenario_run_id", table_name="queue_items")
    op.drop_column("queue_items", "scenario_action_id")
    op.drop_column("queue_items", "scenario_run_id")
    op.drop_table("scenario_run_stands")
    op.drop_table("scenario_runs")
