"""os version bootstrap passwords table

Revision ID: d2448cb78942
Revises: 363a3e5f2444
Create Date: 2026-09-03 00:00:00.000000

Bootstrap-пароль конкретной версии каталога ОС (1:1 с `os_versions`), нужен
для авто-`server.prepare`, который стартует сразу после успешного restore
снимка ACS: восстановление переписывает диск целиком, старый управляющий
SSH-ключ DBOS не переживает reimage, а привязанный `server_account` пропадает
вместе с диском. Пароль шифруется тем же AES-256-GCM конвертом, что и
остальные секреты server_service.

Таблица пустая по умолчанию — admin заполняет вручную через
`PUT /os-versions/{id}/bootstrap-password`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2448cb78942"
down_revision: Union[str, None] = "363a3e5f2444"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "os_version_bootstrap_passwords",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "os_version_id",
            sa.String(length=64),
            sa.ForeignKey("os_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ssh_username", sa.String(length=128), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "os_version_id", name="uq_os_version_bootstrap_password_os_version"
        ),
    )
    op.create_index(
        "ix_os_version_bootstrap_passwords_os_version_id",
        "os_version_bootstrap_passwords",
        ["os_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_os_version_bootstrap_passwords_os_version_id",
        table_name="os_version_bootstrap_passwords",
    )
    op.drop_table("os_version_bootstrap_passwords")
