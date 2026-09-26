"""test_definitions.short_name and dates_quoting

Revision ID: e4a7c2d9b813
Revises: d5c2e8a41f93
Create Date: 2026-09-24 12:00:00.000000

* `short_name` (D6) — короткое имя теста, легаси-ключ словаря `tests`
  (`emm/allta_app_full/allta_image_conf.py:237-305`: `'file system
  benchmark. XFS': 'XFS'`). Его подставляет переменная `TEST_SHORT_NAME`
  (сид `tp01_seed_legacy_formulas`, `fallback: full_name`) в
  `--confluence-new-page` (`backup_image.py:297`). Значения для уже
  импортированного каталога проставляет следующая ревизия
  (`tp02_catalog_parity_data`).
* `dates_quoting` (D4) — `shell` | `legacy` | `raw`, по умолчанию `shell`:
  `run.py` веток подставляет `dates.conf` через `shell=True`, и без кавычек
  значения с пробелами (`-tcas file system benchmark. XFS`) разваливаются на
  несколько аргументов. `legacy` повторяет `backup_image.py:296-308` (двойные
  кавычки у значений с пробелом), `raw` — прежнее поведение testing_service.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4a7c2d9b813"
down_revision: Union[str, None] = "d5c2e8a41f93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_definitions", sa.Column("short_name", sa.String(length=64), nullable=True))
    op.add_column(
        "test_definitions",
        sa.Column("dates_quoting", sa.String(length=16), nullable=False, server_default="shell"),
    )
    op.create_check_constraint(
        "ck_test_definitions_dates_quoting", "test_definitions",
        "dates_quoting IN ('shell', 'legacy', 'raw')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_test_definitions_dates_quoting", "test_definitions", type_="check")
    op.drop_column("test_definitions", "dates_quoting")
    op.drop_column("test_definitions", "short_name")
