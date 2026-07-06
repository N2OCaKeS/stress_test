"""add nav_link (configurable web-UI left-panel button)

Revision ID: u8v9w0x1y2z3
Revises: t7u8v9w0x1y2
Create Date: 2026-07-05 00:00:00.000000

Single-row конфиг настраиваемой кнопки левой панели web-UI. account_admin задаёт
подпись, внешний URL, флаг видимости и список отделов. Строку намеренно НЕ сидим —
пока её нет, кнопка не показывается никому.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "u8v9w0x1y2z3"
down_revision: Union[str, None] = "t7u8v9w0x1y2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nav_link",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("all_departments", sa.Boolean(), nullable=False),
        sa.Column(
            "department_ids",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("nav_link")
