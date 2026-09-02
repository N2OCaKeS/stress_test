"""add oauth_refresh_tokens (rotating OAuth2 refresh + reuse-detection)

Revision ID: q4r5s6t7u8v9
Revises: p3q4r5s6t7u8
Create Date: 2026-06-13 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q4r5s6t7u8v9"
down_revision: Union[str, None] = "p3q4r5s6t7u8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ротируемый OAuth2 refresh: opaque-токен, в БД только hash. Зеркалит
    # таблицу `sessions` (user-сессионный refresh) — sliding-window истории
    # previous-hash'ей для reuse-detection, привязка к (client_id, user_id),
    # снапшот approved scope'ов.
    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=256), nullable=False),
        sa.Column("previous_token_hashes", postgresql.ARRAY(sa.String(length=256)), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("token_generation", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_suspicious", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["oauth_clients.client_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_oauth_refresh_tokens_client_id"), "oauth_refresh_tokens", ["client_id"], unique=False
    )
    op.create_index(
        op.f("ix_oauth_refresh_tokens_user_id"), "oauth_refresh_tokens", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_oauth_refresh_tokens_refresh_token_hash"),
        "oauth_refresh_tokens",
        ["refresh_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_oauth_refresh_tokens_refresh_token_hash"), table_name="oauth_refresh_tokens")
    op.drop_index(op.f("ix_oauth_refresh_tokens_user_id"), table_name="oauth_refresh_tokens")
    op.drop_index(op.f("ix_oauth_refresh_tokens_client_id"), table_name="oauth_refresh_tokens")
    op.drop_table("oauth_refresh_tokens")
