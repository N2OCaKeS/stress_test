"""add visible_to_dept to credentials

Revision ID: b2a4d9e1c815
Revises: a1f3d8c2b740
Create Date: 2026-06-08 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2a4d9e1c815"
down_revision: Union[str, None] = "a1f3d8c2b740"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column(
            "visible_to_dept",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("credentials", "visible_to_dept")
