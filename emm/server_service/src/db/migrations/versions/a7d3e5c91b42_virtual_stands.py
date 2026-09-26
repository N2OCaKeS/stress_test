"""virtual stands — VM service reservation, VM prepare-for-test, snapshot templates

Revision ID: a7d3e5c91b42
Revises: f5c3a8d1e720
Create Date: 2026-09-24 00:00:00.000000

* `vms` — сервисная бронь, зеркало `servers.busy_*`: `service_busy_state`
  (`acs`/`testing`/`busy`/`testing_done`, NULL — брони нет),
  `busy_service_name`, `busy_note`, `service_busy_since`. `vms.busy_state`
  не подходит: это lifecycle-lock, его снимают callback'и воркера.
* `server_prepare_for_test_requests` — цель подготовки: `server_id` или
  `vm_id` (ровно одно), `vm_snapshot_name`; шаг `vm_revert` в CHECK
  `failed_step`; один активный запрос на ВМ.
* `vm_test_settings` — шаблоны имени снимка ВМ для отката перед тестом.
  Легаси: снимок ВМ назван версией целиком —
  `emm/allta_app_full/allta_conf.json` (`cz_comm.stand1`: `"1.7.5.9":
  "1.7.5.9"`), откат `virsh snapshot-revert --snapshotname <версия>` —
  `emm/allta_app_full/libs/liballta.py:1503-1508`. Поэтому первый шаблон —
  `{version}`; второй — `{version}_{mode}`, эталоны VM-домена
  (`server_worker/src/tasks/vms.py::_build_single`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7d3e5c91b42"
down_revision: Union[str, None] = "f5c3a8d1e720"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STEPS = (
    "('restore', 'prepare', 'user_provision', 'pam_fix', 'stand_setup', "
    "'kernel_change', 'mode_switch', 'reboot_verify')"
)
_NEW_STEPS = (
    "('restore', 'vm_revert', 'prepare', 'user_provision', 'pam_fix', "
    "'stand_setup', 'kernel_change', 'mode_switch', 'reboot_verify')"
)
_TABLE = "server_prepare_for_test_requests"


def upgrade() -> None:
    op.add_column("vms", sa.Column("service_busy_state", sa.String(length=16), nullable=True))
    op.add_column("vms", sa.Column("busy_service_name", sa.String(length=64), nullable=True))
    op.add_column("vms", sa.Column("busy_note", sa.String(length=512), nullable=True))
    op.add_column("vms", sa.Column("service_busy_since", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_vms_service_busy_state", "vms",
        "service_busy_state IS NULL OR service_busy_state IN ('acs', 'testing', 'busy', 'testing_done')",
    )
    op.create_check_constraint(
        "ck_vms_service_busy_holder", "vms",
        "(service_busy_state IS NULL) = (busy_service_name IS NULL)",
    )

    op.alter_column(_TABLE, "server_id", existing_type=sa.String(length=64), nullable=True)
    op.add_column(
        _TABLE,
        sa.Column(
            "vm_id", sa.String(length=64),
            sa.ForeignKey("vms.id", ondelete="CASCADE"), nullable=True,
        ),
    )
    op.create_index(f"ix_{_TABLE}_vm_id", _TABLE, ["vm_id"])
    op.add_column(_TABLE, sa.Column("vm_snapshot_name", sa.String(length=255), nullable=True))
    op.create_check_constraint(
        "ck_prepare_for_test_one_target", _TABLE, "(server_id IS NULL) <> (vm_id IS NULL)",
    )
    op.drop_constraint("ck_prepare_for_test_failed_step", _TABLE, type_="check")
    op.create_check_constraint(
        "ck_prepare_for_test_failed_step", _TABLE,
        f"failed_step IS NULL OR failed_step IN {_NEW_STEPS}",
    )
    op.create_index(
        "uq_prepare_for_test_active_vm", _TABLE, ["vm_id"], unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )

    op.create_table(
        "vm_test_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("snapshot_name_templates", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )
    op.execute(
        "INSERT INTO vm_test_settings (id, snapshot_name_templates) "
        "VALUES ('default', '[\"{version}\", \"{version}_{mode}\"]'::jsonb)"
    )


def downgrade() -> None:
    op.drop_table("vm_test_settings")
    op.drop_index("uq_prepare_for_test_active_vm", table_name=_TABLE)
    op.execute(f"DELETE FROM {_TABLE} WHERE vm_id IS NOT NULL")
    op.drop_constraint("ck_prepare_for_test_failed_step", _TABLE, type_="check")
    op.create_check_constraint(
        "ck_prepare_for_test_failed_step", _TABLE,
        f"failed_step IS NULL OR failed_step IN {_OLD_STEPS}",
    )
    op.drop_constraint("ck_prepare_for_test_one_target", _TABLE, type_="check")
    op.drop_column(_TABLE, "vm_snapshot_name")
    op.drop_index(f"ix_{_TABLE}_vm_id", table_name=_TABLE)
    op.drop_column(_TABLE, "vm_id")
    op.alter_column(_TABLE, "server_id", existing_type=sa.String(length=64), nullable=False)

    op.drop_constraint("ck_vms_service_busy_holder", "vms", type_="check")
    op.drop_constraint("ck_vms_service_busy_state", "vms", type_="check")
    op.drop_column("vms", "service_busy_since")
    op.drop_column("vms", "busy_note")
    op.drop_column("vms", "busy_service_name")
    op.drop_column("vms", "service_busy_state")
