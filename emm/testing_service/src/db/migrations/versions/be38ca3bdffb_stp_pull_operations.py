"""stp pull operations

Revision ID: be38ca3bdffb
Revises: e4b8f2a91c67
Create Date: 2026-09-16 22:00:00.000000

§D8 плана миграции: «Pull СТП из life» (`services/stp_pull_from_life.py`) —
обратное направление к `stp_add_test_operations`, читает уже существующие
Zephyr test-run'ы отдела и заводит/сверяет локальные stp_test_run/
stp_test_case/stp_cell. `stp_pull_operations` несёт по одной строке на
`(department_id, os_version_id, zephyr_test_run_key)` — память последней
попытки: что нашли/завели, был ли сетевой сбой. Никакой записи обратно в
Zephyr эта операция не делает вовсе, поэтому шаговых булевых флагов здесь
меньше, чем у `stp_add_test_operations`.

Права переиспользуют уже выданный `(stp_test_run, admin, create)` — тот же
create-жест над `stp_test_run`, что и у /stp/generate и /stp/test-runs/{id}/add-test.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "be38ca3bdffb"
down_revision: Union[str, None] = "e4b8f2a91c67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stp_pull_operations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("os_version_id", sa.String(length=64), nullable=False),
        sa.Column("zephyr_test_run_key", sa.String(length=32), nullable=False),
        sa.Column(
            "stp_test_run_id", sa.String(length=64),
            sa.ForeignKey("stp_test_runs.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("run_upserted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cells_synced", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("result_summary", sa.Text(), nullable=True),
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
        "ix_stp_pull_operations_department_id", "stp_pull_operations", ["department_id"],
    )
    op.create_index(
        "ix_stp_pull_operations_os_version_id", "stp_pull_operations", ["os_version_id"],
    )
    op.create_index(
        "ix_stp_pull_operations_zephyr_test_run_key", "stp_pull_operations", ["zephyr_test_run_key"],
    )
    op.create_unique_constraint(
        "uq_stp_pull_op_dept_os_key", "stp_pull_operations",
        ["department_id", "os_version_id", "zephyr_test_run_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_stp_pull_op_dept_os_key", "stp_pull_operations", type_="unique")
    op.drop_index("ix_stp_pull_operations_zephyr_test_run_key", table_name="stp_pull_operations")
    op.drop_index("ix_stp_pull_operations_os_version_id", table_name="stp_pull_operations")
    op.drop_index("ix_stp_pull_operations_department_id", table_name="stp_pull_operations")
    op.drop_table("stp_pull_operations")
