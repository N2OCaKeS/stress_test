"""partial UNIQUE на (user_id/bot_id, name) WHERE revoked_at IS NULL

Revision ID: d1c2b3a4f5e6
Revises: b8e7c9d4f12a
Create Date: 2026-05-30 00:00:00.000000

Без partial-WHERE имя PAT / bot-токена остаётся «занятым» в БД до retention,
даже после revoke'а — UNIQUE constraint считает revoked-строки. Code-уровень
exists_name уже фильтрует revoked, но constraint всё равно блокирует
штатный flow ротации (revoke old → mint new same-name).

PostgreSQL partial unique index имитирует тот же инвариант, что и
constraint, но только для активных строк.

"""
from typing import Sequence, Union

from alembic import op

revision: str = "d1c2b3a4f5e6"
down_revision: Union[str, None] = "b8e7c9d4f12a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # personal_access_tokens
    op.drop_constraint(
        "uq_pat_user_name", "personal_access_tokens", type_="unique"
    )
    op.create_index(
        "uq_pat_user_name_active",
        "personal_access_tokens",
        ["user_id", "name"],
        unique=True,
        postgresql_where="revoked_at IS NULL",
    )

    # bot_tokens
    op.drop_constraint("uq_bot_token_name", "bot_tokens", type_="unique")
    op.create_index(
        "uq_bot_token_name_active",
        "bot_tokens",
        ["bot_id", "name"],
        unique=True,
        postgresql_where="revoked_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_bot_token_name_active", table_name="bot_tokens")
    op.create_unique_constraint(
        "uq_bot_token_name", "bot_tokens", ["bot_id", "name"]
    )

    op.drop_index(
        "uq_pat_user_name_active", table_name="personal_access_tokens"
    )
    op.create_unique_constraint(
        "uq_pat_user_name", "personal_access_tokens", ["user_id", "name"]
    )
