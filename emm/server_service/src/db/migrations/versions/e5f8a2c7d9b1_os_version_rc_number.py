"""os_version rc_number

Номер РЦ (legacy `"RC3"`) — короткая метка, которую владелец присваивает
версии вручную (в allta_app — команда telegram-бота `/addrc`/`/adduurc`,
чисто ручной ввод без алгоритмического источника). testing_service
использует её как префикс заголовка end-of-run комментария в Confluence
(`"RC3 оперативного обновления Astra Linux SE 1.8.6"`), отдельно от самой
версии.

Nullable, без дефолта и бэкафилла — существующие версии остаются без метки,
пока владелец не проставит её вручную.

Revision ID: e5f8a2c7d9b1
Revises: a1c7e3f9b2d4
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f8a2c7d9b1"
down_revision: Union[str, None] = "a1c7e3f9b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "os_versions",
        sa.Column("rc_number", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("os_versions", "rc_number")
