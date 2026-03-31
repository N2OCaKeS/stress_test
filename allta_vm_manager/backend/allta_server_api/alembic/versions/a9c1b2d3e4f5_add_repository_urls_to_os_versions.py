"""add repository urls to os versions

Revision ID: a9c1b2d3e4f5
Revises: f2a9c4d7e6b1
Create Date: 2026-03-30 13:35:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9c1b2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "f2a9c4d7e6b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "os_versions",
        sa.Column("repository_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.alter_column("os_versions", "repository_urls", server_default=None)


def downgrade() -> None:
    op.drop_column("os_versions", "repository_urls")
