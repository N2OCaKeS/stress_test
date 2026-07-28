"""password policy settings singleton (auth login passwords)

Revision ID: w0x1y2z3a4b5
Revises: v9w0x1y2z3a4
Create Date: 2026-07-28 00:00:00.000000

Платформенный singleton настраиваемой парольной политики логина: минимальная
длина + обязательность буквы/цифры для пользовательских паролей (create/reset).
Настройка в БД (а не в коде), чтобы менять требования без передеплоя. Одна
строка на всю платформу, PK фиксирован значением `default`.

Строку НЕ сидим здесь: на первом запуске её создаёт lifespan из env
(`AUTH_PASSWORD_POLICY_*`), дальше значение живёт в БД.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w0x1y2z3a4b5"
down_revision: Union[str, None] = "v9w0x1y2z3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_policy_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("min_length", sa.Integer(), nullable=False, server_default="12"),
        sa.Column(
            "require_letter", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "require_digit", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("password_policy_settings")
