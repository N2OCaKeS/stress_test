"""git credential id

Revision ID: b9e4c7d21a58
Revises: a4d1f70b39c8
Create Date: 2026-09-18 10:00:00.000000

Клонирование на стенде и REST-запросы HR-отчёта к Bitbucket требуют разных
форматов одного и того же «гитового» секрета: первое подставляет значение
целиком в заголовок `Authorization` (нужна схема — `Bearer <PAT>`), второе
шлёт его как пароль basic-auth. Одна запись обе роли обслужить не может,
поэтому у git-заголовка появляется собственная ссылка на credential. Пусто —
поведение как раньше, `bitbucket_credential_id`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9e4c7d21a58"
down_revision: Union[str, None] = "f1b6d3a07c85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "department_integration_settings",
        sa.Column("git_credential_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("department_integration_settings", "git_credential_id")
