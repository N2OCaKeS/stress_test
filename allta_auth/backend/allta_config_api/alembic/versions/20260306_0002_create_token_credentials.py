"""create token credentials table

Revision ID: 20260306_0002
Revises: 20260227_0001
Create Date: 2026-03-06 15:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260306_0002"
down_revision: Union[str, Sequence[str], None] = "20260227_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("token_key", sa.String(length=120), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_token_credentials_id", "token_credentials", ["id"], unique=False)
    op.create_index("ix_token_credentials_token_key", "token_credentials", ["token_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_token_credentials_token_key", table_name="token_credentials")
    op.drop_index("ix_token_credentials_id", table_name="token_credentials")
    op.drop_table("token_credentials")
