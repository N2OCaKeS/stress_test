"""session previous_token_hashes array + bot_accounts.name unique

Revision ID: f3a4b5c6d7e8
Revises: e9b1c2d3a4f5
Create Date: 2026-05-29 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e9b1c2d3a4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sessions: расширяем reuse-detection с одного предыдущего хэша до N
    # поколений. Старый `previous_token_hash` хранил только один шаг назад
    # — refresh, ротированный больше одного раза, уже не ловился. Новый
    # массив получает наполнение из старого столбца для существующих
    # строк; индекс по GIN — для поиска `<hash> = ANY(previous_token_hashes)`.
    op.add_column(
        "sessions",
        sa.Column(
            "previous_token_hashes",
            ARRAY(sa.String(length=256)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.execute(
        """
        UPDATE sessions
        SET previous_token_hashes = ARRAY[previous_token_hash]
        WHERE previous_token_hash IS NOT NULL
        """
    )
    op.create_index(
        "ix_sessions_previous_token_hashes",
        "sessions",
        ["previous_token_hashes"],
        unique=False,
        postgresql_using="gin",
    )

    # Bot accounts: глобальная уникальность имени. Docker basic-auth
    # лоокаунит бота по `bot.name` (см. docker_registry_service.first_by_name);
    # дубликаты приводили бы к лоокауту/анлоку первого попавшегося, что ломает
    # симметрию с пользовательским brute-force путём.
    op.create_unique_constraint(
        "uq_bot_accounts_name",
        "bot_accounts",
        ["name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_bot_accounts_name", "bot_accounts", type_="unique")
    op.drop_index("ix_sessions_previous_token_hashes", table_name="sessions")
    op.drop_column("sessions", "previous_token_hashes")
