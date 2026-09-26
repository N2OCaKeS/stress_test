"""Scenario stands: skip_pam_fix

Revision ID: d4b8e2f6a193
Revises: c3e8a1f5d207
Create Date: 2026-09-26 00:00:00.000000

* `scenario_stands.skip_pam_fix` — подготовка стенда без PAM-правки
  (`pam_lastlog.so inactive=`), что бы ни включал профиль подготовки.
  От `preparation` не зависит. Легаси: `emm/allta_app_full/backup_image.py:806-812`
  (`run_provision.modes = False` снимал и смену режима, и PAM-правку).
* `scenario_run_stands.skip_pam_fix` — снимок флага на момент запуска.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4b8e2f6a193"
down_revision: Union[str, None] = "c3e8a1f5d207"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("scenario_stands", "scenario_run_stands")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table, sa.Column("skip_pam_fix", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "skip_pam_fix")
