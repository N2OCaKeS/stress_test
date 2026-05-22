"""add code_challenge / code_challenge_method to oauth_authorization_codes (RFC 7636 PKCE)

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
Create Date: 2026-05-20 12:05:01.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PKCE (RFC 7636): public-clients (SPA/CLI) при выписке кода передают
    # `code_challenge` (+ метод S256/plain). На обмене кода `/token` клиент
    # передаёт `code_verifier`, который должен совпадать с challenge.
    # Поля NULL для legacy confidential-client'ов без PKCE (back-compat).
    op.add_column(
        "oauth_authorization_codes",
        sa.Column("code_challenge", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "oauth_authorization_codes",
        sa.Column("code_challenge_method", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_authorization_codes", "code_challenge_method")
    op.drop_column("oauth_authorization_codes", "code_challenge")
