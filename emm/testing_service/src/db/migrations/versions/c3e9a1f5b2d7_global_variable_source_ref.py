"""global_variables.source_ref

Revision ID: c3e9a1f5b2d7
Revises: b7b483209bf8
Create Date: 2026-09-24 10:00:00.000000

`source_ref` — на что ссылается переменная в своём источнике (CONTRACTS.md
C1): шаблон, поле теста, поле стенда, поле интеграций отдела, поле версии
ОС. Форма зависит от `source` и проверяется
`services/variable_resolver.validate_source_ref` при сохранении. Колонка
необязательная: у существующих строк (`launch_context`, `static` без
значения, `per_test_override`, `secret_service`) она `NULL`, и их резолв не
меняется.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e9a1f5b2d7"
down_revision: Union[str, None] = "b7b483209bf8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "global_variables",
        sa.Column("source_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("global_variables", "source_ref")
