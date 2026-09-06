"""host services settings (SSH access for host-service control)

Revision ID: a9d3f7c2e6b1
Revises: e8f3a1c6d2b4
Create Date: 2026-09-06 00:00:00.000000

Платформенный singleton `host_services_settings` — host/port/user + зашифрованный
приватный SSH-ключ дедicated-аккаунта на хосте emm, используется для чтения
статуса и старта/стопа/рестарта ALLTA systemd-юнитов через forced-command
guard (`scripts/host-control/emm-host-service-guard.sh`). По образцу
`acs_settings` (url + зашифрованный пароль).

Actions/audit-каталог не трогаем — событие `settings.host_services_updated`
и `host_service.control` добавлены в `audit_events.py` отдельно.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3f7c2e6b1"
down_revision: Union[str, None] = "e8f3a1c6d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "host_services_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("ssh_host", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("ssh_user", sa.String(length=64), nullable=True),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("host_services_settings")
