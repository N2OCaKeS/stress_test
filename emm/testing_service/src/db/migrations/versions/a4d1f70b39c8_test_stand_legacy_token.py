"""test stand legacy token

Revision ID: a4d1f70b39c8
Revises: c7e1b94a2d38
Create Date: 2026-09-17 19:30:00.000000

`test_stands.legacy_token` — имя стенда в allta_app (`stand3`..`stand14`).

Внутренний `test_stands.id` — `stand_<32hex>`, и он не годится там, где
легаси оперирует именем: имя Zephyr-рана (`{version}_{mode}_{kernel}_stand3`),
строка «№ стенда» в СТП-матрице, позиционный `-sn` в команде теста (целевые
скрипты объявляют его через `choices=['1','3','4',...]`, то есть принимают
только голый номер). NULL допустим — стенд, заведённый уже в emm, легаси-имени
не имеет; потребители в этом случае откатываются на `id`.

UNIQUE — два стенда не могут быть одним и тем же `standN`. В Postgres NULL не
конфликтует с NULL, поэтому безымянных стендов может быть сколько угодно.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4d1f70b39c8"
down_revision: Union[str, None] = "c7e1b94a2d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_stands",
        sa.Column("legacy_token", sa.String(length=32), nullable=True),
    )
    op.create_unique_constraint(
        "uq_test_stands_legacy_token", "test_stands", ["legacy_token"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_test_stands_legacy_token", "test_stands", type_="unique")
    op.drop_column("test_stands", "legacy_token")
