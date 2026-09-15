"""test timeout seconds

Revision ID: e2c7a94f1b83
Revises: b7a4d2c918e5
Create Date: 2026-09-15 00:00:00.000000

`test_definitions.timeout_seconds` — переопределение общего SSH-таймаута
testing_worker'а (`Settings.ssh_command_timeout_seconds`) для конкретного
теста. NULL — берётся дефолт воркера, как и раньше.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2c7a94f1b83"
down_revision: Union[str, None] = "b7a4d2c918e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_definitions",
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_definitions", "timeout_seconds")
