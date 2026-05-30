"""server_account ssh key pair (public + encrypted private)

Revision ID: b9c2e7d4a8f1
Revises: a8d2b7c1e394
Create Date: 2026-05-30 12:00:00.000000

Аккаунт получает пару SSH-ключей вдобавок к паролю. Public — в открытом виде,
едет в `~/.ssh/authorized_keys` на боксе при provision'е. Private — шифруется
тем же `secrets_service` форматом, что и пароль, по отдельному AAD.

Оба поля nullable: managed-аккаунты, заведённые до фичи, и discovered-аккаунты
их не имеют до первого provision'а — на этом вызове ключи генерируются и
сохраняются.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9c2e7d4a8f1"
down_revision: Union[str, None] = "a8d2b7c1e394"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "server_accounts",
        sa.Column("ssh_public_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "server_accounts",
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("server_accounts", "ssh_private_key_encrypted")
    op.drop_column("server_accounts", "ssh_public_key")
