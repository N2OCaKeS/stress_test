"""changelog cache

Revision ID: f3a1d8c6b4e2
Revises: e7c2a49f18b6
Create Date: 2026-09-10 16:00:00.000000

Восьмой домен testing_service (§2.7, §7 плана миграции, волна 9): кэш ответа
внешнего `changelog.service` по `build_version`, чтобы changelog-фильтр
генерации СТП (`services/changelog_service.py`) не бил по сети на каждый
вызов `/stp/generate`. TTL практически бессрочный (RC не переиздаётся задним
числом) — контролируется настройкой `changelog_cache_ttl_seconds`, не схемой.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a1d8c6b4e2"
down_revision: Union[str, None] = "e7c2a49f18b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "changelog_cache",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("build_version", sa.String(length=64), nullable=False),
        sa.Column("response_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_changelog_cache_build_version", "changelog_cache", ["build_version"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_changelog_cache_build_version", table_name="changelog_cache")
    op.drop_table("changelog_cache")
