"""create service credentials table

Revision ID: 20260227_0001
Revises: 
Create Date: 2026-02-27 11:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260227_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("service_name", sa.String(length=120), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password", sa.String(length=255), nullable=False),
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
    op.create_index("ix_service_credentials_id", "service_credentials", ["id"], unique=False)
    op.create_index(
        "ix_service_credentials_service_name",
        "service_credentials",
        ["service_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_service_credentials_service_name", table_name="service_credentials")
    op.drop_index("ix_service_credentials_id", table_name="service_credentials")
    op.drop_table("service_credentials")
