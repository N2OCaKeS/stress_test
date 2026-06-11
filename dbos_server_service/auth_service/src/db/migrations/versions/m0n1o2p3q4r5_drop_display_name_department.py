"""collapse Department.display_name into name

Revision ID: m0n1o2p3q4r5
Revises: l9m0n1o2p3q4
Create Date: 2026-06-11 12:00:00.000000

Сливаем `departments.display_name` в `departments.name`: identifier-как-slug
больше не нужен — он живёт через `id`, а пользователь видит и редактирует
только человеческое имя. Перед DROP'ом переносим непустые `display_name` в
`name`, чтобы UI не потерял текущие подписи.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "m0n1o2p3q4r5"
down_revision: Union[str, None] = "l9m0n1o2p3q4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE departments "
        "SET name = display_name "
        "WHERE display_name IS NOT NULL AND TRIM(display_name) != ''"
    )
    op.drop_column("departments", "display_name")


def downgrade() -> None:
    op.add_column(
        "departments",
        sa.Column("display_name", sa.String(length=256), nullable=True),
    )
    op.execute("UPDATE departments SET display_name = name")
    op.alter_column("departments", "display_name", nullable=False)
