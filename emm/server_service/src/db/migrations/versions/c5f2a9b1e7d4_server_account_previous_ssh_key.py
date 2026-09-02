"""retain previous server_account ssh key during rotation

Симметрично `previous_password_encrypted`: при ротации ssh-ключа учётки текущий
приватный ключ (тем же envelope и AAD, что `ssh_private_key_encrypted`)
переезжает в `previous_ssh_private_key_encrypted`, а рядом фиксируется
`previous_ssh_key_rotated_at`. Прежний ключ остаётся доступным на время
переходного периода, пока новый не раскатан на серверы. Обе колонки nullable
без default — у существующих строк переходного периода нет.

Revision ID: c5f2a9b1e7d4
Revises: a1f7c2e9d4b6
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5f2a9b1e7d4"
down_revision: Union[str, None] = "a1f7c2e9d4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "server_accounts",
        sa.Column("previous_ssh_private_key_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "server_accounts",
        sa.Column(
            "previous_ssh_key_rotated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("server_accounts", "previous_ssh_key_rotated_at")
    op.drop_column("server_accounts", "previous_ssh_private_key_encrypted")
