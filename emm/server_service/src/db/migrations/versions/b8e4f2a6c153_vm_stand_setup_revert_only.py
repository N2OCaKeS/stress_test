"""VM stand setup without revert, revert_only preparation

Revision ID: b8e4f2a6c153
Revises: a7d3e5c91b42
Create Date: 2026-09-25 00:00:00.000000

* `server_stand_setup_requests` — цель: `server_id` или `vm_id` (ровно
  одно), как у `server_prepare_for_test_requests` в `a7d3e5c91b42`.
  Настройка ВМ-стенда между ступенями многоступенчатого теста
  (`POST /internal/vms/{id}/stand-setup`).
* `server_prepare_for_test_requests.preparation` — `full` / `revert_only`.
  `revert_only` не меняет режим безопасности и не выполняет шаг настройки
  стенда. Легаси: `emm/allta_app_full/backup_image.py::freeipa_authentication_test`
  (`run_provision.modes = False` у клиента FreeIPA).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4f2a6c153"
down_revision: Union[str, None] = "a7d3e5c91b42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SETUP = "server_stand_setup_requests"
_PREPARE = "server_prepare_for_test_requests"


def upgrade() -> None:
    op.alter_column(_SETUP, "server_id", existing_type=sa.String(length=64), nullable=True)
    op.add_column(
        _SETUP,
        sa.Column("vm_id", sa.String(length=64), sa.ForeignKey("vms.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index(f"ix_{_SETUP}_vm_id", _SETUP, ["vm_id"])
    op.create_check_constraint("ck_stand_setup_one_target", _SETUP, "(server_id IS NULL) <> (vm_id IS NULL)")

    op.add_column(
        _PREPARE,
        sa.Column("preparation", sa.String(length=16), nullable=False, server_default="full"),
    )
    op.create_check_constraint(
        "ck_prepare_for_test_preparation", _PREPARE, "preparation IN ('full', 'revert_only')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_prepare_for_test_preparation", _PREPARE, type_="check")
    op.drop_column(_PREPARE, "preparation")

    op.execute(f"DELETE FROM {_SETUP} WHERE vm_id IS NOT NULL")
    op.drop_constraint("ck_stand_setup_one_target", _SETUP, type_="check")
    op.drop_index(f"ix_{_SETUP}_vm_id", table_name=_SETUP)
    op.drop_column(_SETUP, "vm_id")
    op.alter_column(_SETUP, "server_id", existing_type=sa.String(length=64), nullable=False)
