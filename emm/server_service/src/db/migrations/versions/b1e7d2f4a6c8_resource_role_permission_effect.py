"""resource_role_permissions.effect (allow/deny override)

Revision ID: b1e7d2f4a6c8
Revises: a4d9f3e8c1b7
Create Date: 2026-06-30 10:00:00.000000

Инстанс-уровневый ACL становится не только аддитивным, но и переопределяющим:
точечная строка на конкретный ресурс может ДОБАВить право поверх тип-wide
матрицы (``allow``) либо ЗАПРЕТить его этой роли на этом ресурсе (``deny``).

Колонка ``effect`` — строка ``allow`` | ``deny`` с дефолтом ``allow``. Существующие
строки бэкфиллятся в ``allow`` через server_default (старая семантика — чистый
union). CHECK ``ck_resource_role_permissions_effect`` держит домен значений.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1e7d2f4a6c8"
down_revision: Union[str, None] = "a4d9f3e8c1b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "resource_role_permissions",
        sa.Column(
            "effect",
            sa.String(length=16),
            nullable=False,
            server_default="allow",
        ),
    )
    op.create_check_constraint(
        "ck_resource_role_permissions_effect",
        "resource_role_permissions",
        "effect IN ('allow', 'deny')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_resource_role_permissions_effect",
        "resource_role_permissions",
        type_="check",
    )
    op.drop_column("resource_role_permissions", "effect")
