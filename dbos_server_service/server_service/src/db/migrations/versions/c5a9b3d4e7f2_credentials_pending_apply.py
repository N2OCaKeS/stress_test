"""credentials_pending_apply flag for server_accounts and ipmi_controllers

Revision ID: c5a9b3d4e7f2
Revises: b9c2e7d4a8f1
Create Date: 2026-06-01 12:00:00.000000

Двухфазный creds-drift guard: между dispatch'ем ротации/provision'а и приходом
worker-callback'а серверная БД уже хранит свежий ciphertext, а боксок ещё
живёт со старым (в худшем — drift до тех пор, пока callback не приедет).
Колонка ставится в `True` при dispatch'е и снимается callback'ом; retry до
прихода callback'а считает БД-кред «не подтверждённым» и шлёт worker'у
`force_replace=True`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5a9b3d4e7f2"
down_revision: Union[str, None] = "b9c2e7d4a8f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "server_accounts",
        sa.Column(
            "credentials_pending_apply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "ipmi_controllers",
        sa.Column(
            "credentials_pending_apply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("ipmi_controllers", "credentials_pending_apply")
    op.drop_column("server_accounts", "credentials_pending_apply")
