"""bot_accounts.last_known_ips JSONB

Revision ID: b8e7c9d4f12a
Revises: a4d7e2f9c1b0
Create Date: 2026-05-30 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "b8e7c9d4f12a"
down_revision: Union[str, None] = "a4d7e2f9c1b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_accounts",
        sa.Column(
            "last_known_ips",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("bot_accounts", "last_known_ips")
