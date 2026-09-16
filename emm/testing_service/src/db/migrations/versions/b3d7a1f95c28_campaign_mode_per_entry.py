"""campaign mode moves from test_runs to test_run_entries

Revision ID: b3d7a1f95c28
Revises: a1c5f8e2d640
Create Date: 2026-09-15 00:00:01.000000

Security mode is now a fixed property of each test (`test_definitions.mode`),
not of the campaign as a whole — one campaign can legitimately mix orel and
smolensk tests, each prepared under its own mode. `test_run_entries.mode`
records that per-entry, backfilled here from the entry's `test_definitions`
row (falling back to `orel` if the test was since deleted). `test_runs.mode`
becomes nullable and is no longer written by new campaigns; kept for
backward compatibility with historical rows and any code still reading it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3d7a1f95c28"
down_revision: Union[str, None] = "a1c5f8e2d640"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_run_entries", sa.Column("mode", sa.String(16), nullable=True))
    op.execute("""
        UPDATE test_run_entries e SET mode = COALESCE(
            (SELECT t.mode FROM test_definitions t WHERE t.id = e.test_id),
            'orel'
        )
    """)
    op.alter_column("test_run_entries", "mode", existing_type=sa.String(16), nullable=False)

    op.alter_column("test_runs", "mode", existing_type=sa.String(16), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE test_runs SET mode = 'orel' WHERE mode IS NULL")
    op.alter_column("test_runs", "mode", existing_type=sa.String(16), nullable=False)
    op.drop_column("test_run_entries", "mode")
