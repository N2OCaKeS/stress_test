"""acs settings and department access tables

Revision ID: 3c6094335c43
Revises: d2e6b8f4a1c7
Create Date: 2026-09-03 00:00:00.000000

Настройки доступа к ACS (внешний сервис снимков дисков физических серверов
через Clonezilla): платформенный singleton `acs_settings` (url + зашифрованный
пароль, по образцу `probe_settings`) и маленькая таблица `acs_department_access`
для per-department opt-in (по образцу `auth_service.department_docker_registry`,
но без FK на departments — server_service не пересекает границу БД
auth_service, department_id тут soft-reference, как и `servers.department_id`).

Прав/actions/audit-каталог здесь не трогаем — это отдельная волна.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3c6094335c43"
down_revision: Union[str, None] = "d2e6b8f4a1c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "acs_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("acs_url", sa.String(length=512), nullable=True),
        sa.Column("acs_password_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "acs_department_access",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("department_id", name="uq_acs_department_access_department"),
    )
    op.create_index(
        "ix_acs_department_access_department_id",
        "acs_department_access",
        ["department_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_acs_department_access_department_id", table_name="acs_department_access",
    )
    op.drop_table("acs_department_access")
    op.drop_table("acs_settings")
