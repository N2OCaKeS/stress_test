"""add lockout counters to oauth_clients/bot_accounts + is_public flag

Revision ID: e9b1c2d3a4f5
Revises: d8e9f0a1b2c3
Create Date: 2026-05-29 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9b1c2d3a4f5"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-client lockout для OAuth `client_credentials`: симметрично
    # `users.failed_login_attempts` / `users.locked_until`. Закрывает
    # brute-force через ротацию IP в обход per-IP rate-limit'а.
    op.add_column(
        "oauth_clients",
        sa.Column(
            "failed_secret_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "oauth_clients",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )

    # `is_public` отделяет SPA/CLI-клиентов от confidential. У public
    # выписка кода требует S256 PKCE (plain отвергается). Default False —
    # back-compat для существующих confidential-клиентов.
    op.add_column(
        "oauth_clients",
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Per-bot lockout для `/docker/token`: симметрично user-пути. Bot
    # идентифицируется по username из Basic-auth — первого совпадения
    # bot.name достаточно для регистрации фейла.
    op.add_column(
        "bot_accounts",
        sa.Column(
            "failed_token_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "bot_accounts",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_accounts", "locked_until")
    op.drop_column("bot_accounts", "failed_token_attempts")
    op.drop_column("oauth_clients", "is_public")
    op.drop_column("oauth_clients", "locked_until")
    op.drop_column("oauth_clients", "failed_secret_attempts")
