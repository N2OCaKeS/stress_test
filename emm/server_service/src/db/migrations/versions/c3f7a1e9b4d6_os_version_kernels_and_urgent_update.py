"""os_version kernels + is_urgent_update

Порт двух атрибутов РЦ из легаси allta_app в глобальный каталог os_version:

1. `kernels` — список версий ядер, привязанных к РЦ. Массив строк, ведётся
   вручную (в отличие от `repositories`, автоматическим резолвом с репозитория
   пока не покрыт), NOT NULL, дефолт пустой массив.

2. `is_urgent_update` — булев флаг срочного хотфикса вне обычного цикла РЦ
   (legacy UU, `X.Y.Z.UU.M.N`). Формат имени версии не воспроизводим, только
   факт — NOT NULL, дефолт `false`.

Обе колонки аддитивные, существующие строки получают дефолты через
server_default, без бэкафилла.

Revision ID: c3f7a1e9b4d6
Revises: b7c2e4a9d1f6
Create Date: 2026-09-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3f7a1e9b4d6"
down_revision: Union[str, None] = "b7c2e4a9d1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "os_versions",
        sa.Column(
            "kernels",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "os_versions",
        sa.Column(
            "is_urgent_update",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("os_versions", "is_urgent_update")
    op.drop_column("os_versions", "kernels")
