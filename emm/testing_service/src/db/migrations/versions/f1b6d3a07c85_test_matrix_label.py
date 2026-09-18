"""test matrix label

Revision ID: f1b6d3a07c85
Revises: a4d1f70b39c8
Create Date: 2026-09-18 00:00:00.000000

`test_definitions.matrix_label` — короткая подпись строки в СТП-матрице
(легаси `allta_image_conf.py::testname_columns`, там же «file system
benchmark. EXT4» → «FS_EXT4»). NULL — матрица печатает полное название,
как делала до этой колонки.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1b6d3a07c85"
down_revision: Union[str, None] = "a4d1f70b39c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_definitions",
        sa.Column("matrix_label", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_definitions", "matrix_label")
