"""password policy settings singleton

Revision ID: c4d8e1f9a2b6
Revises: b3d7f1a29c84
Create Date: 2026-07-10 00:00:00.000000

Платформенный singleton настраиваемой парольной политики server-аккаунтов:
минимальная длина + обязательность буквы/цифры для ручного ввода пароля на
create/rotate. Настройка в БД (а не в коде), чтобы менять требования без
передеплоя. Одна строка на всю платформу, PK фиксирован значением `default`;
строка сидится дефолтами (8 символов, буква и цифра обязательны) — историческое
поведение.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d8e1f9a2b6"
down_revision: Union[str, None] = "b3d7f1a29c84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_policy_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("min_length", sa.Integer(), nullable=False, server_default="8"),
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
    # Сид singleton-строки дефолтами. server_default'ы покрывают значения, явно
    # задаём только PK.
    op.execute(
        "INSERT INTO password_policy_settings (id) VALUES ('default')"
    )


def downgrade() -> None:
    op.drop_table("password_policy_settings")
