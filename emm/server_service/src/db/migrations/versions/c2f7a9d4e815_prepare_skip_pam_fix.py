"""prepare-for-test: skip_pam_fix

Revision ID: c2f7a9d4e815
Revises: b8e4f2a6c153
Create Date: 2026-09-26 00:00:00.000000

* `server_prepare_for_test_requests.skip_pam_fix` — не выполнять шаг
  `pam_fix`, даже если его включает профиль подготовки. Флаг стенда
  сценария, от `preparation` не зависит. Легаси:
  `emm/allta_app_full/backup_image.py:806-812` (`run_provision.modes = False`
  снимает и смену режима, и PAM-правку).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2f7a9d4e815"
down_revision: Union[str, None] = "b8e4f2a6c153"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREPARE = "server_prepare_for_test_requests"


def upgrade() -> None:
    op.add_column(
        _PREPARE,
        sa.Column("skip_pam_fix", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column(_PREPARE, "skip_pam_fix")
