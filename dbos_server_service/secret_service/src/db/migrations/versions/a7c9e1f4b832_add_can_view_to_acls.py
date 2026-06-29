"""add can_view to role_acls and user_acls

Третий уровень доступа к секрету: view — актор видит, что секрет есть
(метаданные/листинг), но не видит значение. Лесенка прав: view ⊂ read ⊂ write.

Бэкфилл: существующие грантополучатели с can_read=true должны сохранить
видимость метаданных, поэтому им проставляем can_view=true. Новые строки
стартуют с false (server_default).

Revision ID: a7c9e1f4b832
Revises: f6b8c0a4d275
Create Date: 2026-06-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c9e1f4b832"
down_revision: Union[str, None] = "f6b8c0a4d275"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("role_acls", "user_acls"):
        op.add_column(
            table,
            sa.Column(
                "can_view",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.execute(
            f"UPDATE {table} SET can_view = true WHERE can_read = true"
        )


def downgrade() -> None:
    for table in ("user_acls", "role_acls"):
        op.drop_column(table, "can_view")
