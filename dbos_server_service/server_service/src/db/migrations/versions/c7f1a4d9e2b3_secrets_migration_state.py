"""secrets_migration_state singleton (force-reencrypt flag)

Revision ID: c7f1a4d9e2b3
Revises: b1e7d2f4a6c8
Create Date: 2026-07-02 12:00:00.000000

Durable флаг режима форсированной перешифровки секретов. Пока `force_active`
взведён, maintenance-gate отбивает пользовательские запросы 503'ами до конца
дренажа legacy-ciphertext'ов. Флаг общий для всех реплик и переживает рестарт,
поэтому — в БД, не в памяти процесса.

Таблица держит одну строку `id='singleton'`, засеянную здесь же, чтобы
читатели всегда находили её.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f1a4d9e2b3"
down_revision: Union[str, None] = "b1e7d2f4a6c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "secrets_migration_state",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "force_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Синглтон-строка. Force выключен по умолчанию.
    op.execute(
        "INSERT INTO secrets_migration_state (id, force_active) "
        "VALUES ('singleton', false)"
    )


def downgrade() -> None:
    op.drop_table("secrets_migration_state")
