"""add description to departments

Revision ID: l9m0n1o2p3q4
Revises: k8l9m0n1o2p3
Create Date: 2026-06-11 00:00:00.000000

Колонка `departments.description` (varchar(1024), nullable). Свободно-форматный
текст для UI-карточки отдела — пояснение, контакты, организационный смысл.
Меняется через `PATCH /api/auth/v1/departments/{id}`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "l9m0n1o2p3q4"
down_revision: Union[str, None] = "k8l9m0n1o2p3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "departments",
        sa.Column("description", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("departments", "description")
