"""add first/last/middle name to users

Revision ID: v9w0x1y2z3a4
Revises: u8v9w0x1y2z3
Create Date: 2026-07-08 00:00:00.000000

Три nullable-колонки `users.last_name` / `first_name` / `middle_name`
(String(128)) — ФИО пользователя. В логине не участвуют, уникальности не
требуют; заполняются опционально при создании/обновлении юзера. Существующие
учётки остаются с NULL.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "v9w0x1y2z3a4"
down_revision: Union[str, None] = "u8v9w0x1y2z3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_name", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("first_name", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("middle_name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "middle_name")
    op.drop_column("users", "first_name")
    op.drop_column("users", "last_name")
