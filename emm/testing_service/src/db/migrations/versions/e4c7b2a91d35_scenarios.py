"""Multi-stand scenarios (scenarios, scenario_stands, scenario_actions)

Revision ID: e4c7b2a91d35
Revises: d2b6e8f41a73
Create Date: 2026-09-24 20:00:00.000000

Описание многостендового сценария без исполнения.
Сценарий ссылается на конкретные стенды пула (`test_stands.id`, ролей нет —
решение владельца 24.09), действия упорядочены `position`. Легаси-пример —
FreeIPA (`emm/allta_app_full/backup_image.py:943-964`).

Источник переменных `stand_ref` — новое значение
`global_variables.source`; CHECK на колонке нет, миграция его не трогает.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4c7b2a91d35"
down_revision: Union[str, None] = "d2b6e8f41a73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scenarios",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("code", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("department_id", sa.String(64), nullable=False),
        sa.Column("readiness", sa.String(32), nullable=False, server_default="development"),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "readiness IN ('ready', 'review', 'broken', 'development')", name="ck_scenarios_readiness",
        ),
    )
    op.create_index("ix_scenarios_department_id", "scenarios", ["department_id"])

    op.create_table(
        "scenario_stands",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "scenario_id", sa.String(64), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("stand_id", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("preparation", sa.String(16), nullable=False, server_default="full"),
        sa.Column("provisioning_profile_id", sa.String(64), nullable=True),
        sa.Column("stand_setup", postgresql.JSONB(), nullable=True),
        sa.Column("kernel_override", sa.String(128), nullable=True),
        sa.Column("mode_override", sa.String(16), nullable=True),
        sa.UniqueConstraint("scenario_id", "stand_id", name="uq_scenario_stands_stand"),
        sa.CheckConstraint(
            "preparation IN ('full', 'revert_only', 'none')", name="ck_scenario_stands_preparation",
        ),
    )
    op.create_index("ix_scenario_stands_scenario_id", "scenario_stands", ["scenario_id"])
    op.create_index("ix_scenario_stands_stand_id", "scenario_stands", ["stand_id"])

    op.create_table(
        "scenario_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "scenario_id", sa.String(64), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "scenario_stand_id", sa.String(64),
            sa.ForeignKey("scenario_stands.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("test_id", sa.String(64), nullable=True),
        sa.Column("is_verdict", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("scenario_id", "position", name="uq_scenario_actions_position"),
        sa.CheckConstraint("kind IN ('run_test', 'prepare_stand', 'wait')", name="ck_scenario_actions_kind"),
    )
    op.create_index("ix_scenario_actions_scenario_id", "scenario_actions", ["scenario_id"])
    op.create_index("ix_scenario_actions_scenario_stand_id", "scenario_actions", ["scenario_stand_id"])
    op.create_index("ix_scenario_actions_test_id", "scenario_actions", ["test_id"])


def downgrade() -> None:
    op.drop_table("scenario_actions")
    op.drop_table("scenario_stands")
    op.drop_table("scenarios")
