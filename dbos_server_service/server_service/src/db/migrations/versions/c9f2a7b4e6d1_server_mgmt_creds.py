"""per-server management credentials on servers

Revision ID: c9f2a7b4e6d1
Revises: f1a3c8e2d574
Create Date: 2026-06-29

Каждый сервер несёт свою управляющую SSH-пару + пароль пользователя dbos (#3):
раньше управление шло по единому глобальному ключу из env воркера, теперь
материал генерит server_service и шифрует тем же envelope-форматом
(secrets_service, AES-256-GCM), что и пароли аккаунтов/IPMI.

Public-ключ — открытым текстом (кладётся в authorized_keys, нужен для
отпечатка в UI). Private + password — зашифрованы по своему AAD. previous_*
— переходное окно ротации (анти-локаут). Все поля nullable / с дефолтом:
существующих данных нет, стек переподнимается с нуля, backfill не нужен.

Length-cap CHECK'и (< 8192) — зеркало server_accounts-cap'ов: Ed25519 privkey
~120 байт + envelope, пароль 24 символа, двукратный запас от tooling-bug'а.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9f2a7b4e6d1"
down_revision: Union[str, None] = "f1a3c8e2d574"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("mgmt_ssh_public_key", sa.Text(), nullable=True))
    op.add_column(
        "servers", sa.Column("mgmt_ssh_private_key_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "servers", sa.Column("mgmt_password_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "servers",
        sa.Column("mgmt_creds_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("previous_mgmt_ssh_private_key_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("previous_mgmt_password_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column(
            "mgmt_creds_pending_apply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        "ck_servers_mgmt_ssh_private_key_len",
        "servers",
        "mgmt_ssh_private_key_encrypted IS NULL OR length(mgmt_ssh_private_key_encrypted) < 8192",
    )
    op.create_check_constraint(
        "ck_servers_mgmt_password_len",
        "servers",
        "mgmt_password_encrypted IS NULL OR length(mgmt_password_encrypted) < 8192",
    )


def downgrade() -> None:
    op.drop_constraint("ck_servers_mgmt_password_len", "servers", type_="check")
    op.drop_constraint("ck_servers_mgmt_ssh_private_key_len", "servers", type_="check")
    op.drop_column("servers", "mgmt_creds_pending_apply")
    op.drop_column("servers", "previous_mgmt_password_encrypted")
    op.drop_column("servers", "previous_mgmt_ssh_private_key_encrypted")
    op.drop_column("servers", "mgmt_creds_rotated_at")
    op.drop_column("servers", "mgmt_password_encrypted")
    op.drop_column("servers", "mgmt_ssh_private_key_encrypted")
    op.drop_column("servers", "mgmt_ssh_public_key")
