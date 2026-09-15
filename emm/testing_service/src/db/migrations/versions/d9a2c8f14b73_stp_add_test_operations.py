"""stp add test operations

Revision ID: d9a2c8f14b73
Revises: c4a8e1f7d9b6
Create Date: 2026-09-15 21:00:00.000000

§D6/D7 плана миграции: долговечная операция «добавить один тест EMM в СТП»
(`services/stp_add_test.py`) — узкий, per-test аналог `/stp/generate`.
`stp_add_test_operations` несёт четыре шаговых флага
(`zephyr_testcase_created`/`zephyr_added_to_run`/`stp_cell_created`/
`life_published`) + `status` (`pending`/`succeeded`/`failed`) +
`last_error`, чтобы повторный вызов на ту же пару `(test_definition_id,
stp_test_run_id)` продолжал с первого не пройденного шага, а не дублировал
работу ни локально, ни в Zephyr.

Права переиспользуют уже выданный `(stp_test_run, admin, create)` — новая
операция это тот же create-жест над `stp_test_run`, просто более узкий.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9a2c8f14b73"
down_revision: Union[str, None] = "80c32bfd05d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stp_add_test_operations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column(
            "test_definition_id", sa.String(length=64),
            sa.ForeignKey("test_definitions.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "stp_test_run_id", sa.String(length=64),
            sa.ForeignKey("stp_test_runs.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "stp_test_case_id", sa.String(length=64),
            sa.ForeignKey("stp_test_cases.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "stp_cell_id", sa.String(length=64),
            sa.ForeignKey("stp_cells.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("zephyr_testcase_created", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("zephyr_added_to_run", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("stp_cell_created", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("life_published", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_stp_add_test_operations_department_id", "stp_add_test_operations", ["department_id"],
    )
    op.create_index(
        "ix_stp_add_test_operations_test_definition_id",
        "stp_add_test_operations", ["test_definition_id"],
    )
    op.create_index(
        "ix_stp_add_test_operations_stp_test_run_id",
        "stp_add_test_operations", ["stp_test_run_id"],
    )
    op.create_unique_constraint(
        "uq_stp_add_test_op_test_run", "stp_add_test_operations",
        ["test_definition_id", "stp_test_run_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_stp_add_test_op_test_run", "stp_add_test_operations", type_="unique")
    op.drop_index("ix_stp_add_test_operations_stp_test_run_id", table_name="stp_add_test_operations")
    op.drop_index("ix_stp_add_test_operations_test_definition_id", table_name="stp_add_test_operations")
    op.drop_index("ix_stp_add_test_operations_department_id", table_name="stp_add_test_operations")
    op.drop_table("stp_add_test_operations")
