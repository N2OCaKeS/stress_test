"""test run stp composition snapshot

Revision ID: e4b8f2a91c67
Revises: b3d7a1f95c28
Create Date: 2026-09-16 00:00:01.000000

Full-RC campaigns can now be created without an explicit stand list — the
composition (which tests, therefore which stands and kernels) is derived
from the department's active STP composition for that RC instead. These two
soft-reference columns record which `stp_compositions` row and revision a
derived campaign was built from, purely for audit/traceability: a later STP
composition change must not retroactively reinterpret an already-created
campaign. Both stay NULL for campaigns created from an explicit stand list
(no STP involved) or when no `stp_compositions` row exists yet for that RC.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b8f2a91c67"
down_revision: Union[str, None] = "b3d7a1f95c28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_runs", sa.Column("stp_composition_id", sa.String(64), nullable=True))
    op.add_column("test_runs", sa.Column("stp_revision", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("test_runs", "stp_revision")
    op.drop_column("test_runs", "stp_composition_id")
