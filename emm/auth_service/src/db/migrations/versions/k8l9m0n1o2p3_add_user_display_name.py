"""add display_name to users

Revision ID: k8l9m0n1o2p3
Revises: j7k8l9m0n1o2
Create Date: 2026-06-11 00:00:00.000000

Колонка `users.display_name` (String(256), nullable). Произвольный
человеческий заголовок профиля — ФИО, ник, что угодно, что пользователь
хочет видеть в UI. В отличие от `username` не уникален и не участвует
в логине. Сохраняется через `PATCH /api/auth/v1/me` (self-service).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "k8l9m0n1o2p3"
down_revision: Union[str, None] = "j7k8l9m0n1o2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("display_name", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "display_name")
