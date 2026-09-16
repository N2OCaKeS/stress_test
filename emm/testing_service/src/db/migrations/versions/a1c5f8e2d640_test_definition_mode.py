"""test definition fixed security mode

Revision ID: a1c5f8e2d640
Revises: d9a2c8f14b73
Create Date: 2026-09-15 00:00:00.000000

`test_definitions.mode` — режим безопасности Astra (orel/smolensk), под
которым тест всегда исполняется. Фиксируется на карточке теста при заведении
в каталог, больше не выбирается тем, кто запускает тест или кампанию.
`server_default='orel'` — легаси-конвенция каталога
(`scripts/import_catalog.allta.yaml`): только явно `*.smolensk`/
`*_smolensk`-суффиксные коды были смоленском, всё остальное — орёл, поэтому
существующие строки (61-тестовый импорт allta_app) корректно получают
дефолт без ручной доразметки.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c5f8e2d640"
down_revision: Union[str, None] = "d9a2c8f14b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_definitions",
        sa.Column("mode", sa.String(16), nullable=False, server_default="orel"),
    )
    op.create_check_constraint(
        "ck_test_definitions_mode",
        "test_definitions",
        "mode IN ('orel', 'smolensk')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_test_definitions_mode", "test_definitions", type_="check")
    op.drop_column("test_definitions", "mode")
