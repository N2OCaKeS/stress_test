"""server pre_acs_busy_snapshot column

Adds `servers.pre_acs_busy_snapshot` (JSONB, nullable) — snapshot of
busy_state/busy_user_id/busy_note/busy_since captured right before a server
is put into busy_state=acs, so the ACS gate can restore whatever state the
server was in before the ACS operation instead of unconditionally freeing it.

Revision ID: a4b8f2d1c6e9
Revises: 68c541e6a518
Create Date: 2026-09-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4b8f2d1c6e9"
down_revision: Union[str, None] = "68c541e6a518"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("pre_acs_busy_snapshot", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servers", "pre_acs_busy_snapshot")
