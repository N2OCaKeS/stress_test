"""stand setup step, provisioning profile values, stand-setup requests

Revision ID: f5c3a8d1e720
Revises: e3b9c1d74a26
Create Date: 2026-09-24 00:00:00.000000



* `server_prepare_for_test_requests` — `stand_setup` (шаг настройки стенда
  теста без текста скрипта), `stand_setup_script_encrypted` (скрипт может
  нести секреты), `provisioning` (профиль подготовки: разрешённые упавшие
  юниты, перезагрузки при `degraded`, PAM-правка); новые шаги `pam_fix` и
  `stand_setup` в CHECK `failed_step`. Легаси: `audit=0` —
  `emm/allta_app_full/backup_image.py:715-716`, `degraded`-allowlist —
  `backup_image.py:554-600`, PAM — `backup_image.py:807-808`.
* `server_stand_setup_requests` — операция «настройка без restore»
  (`POST /internal/servers/{id}/stand-setup`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f5c3a8d1e720"
down_revision: Union[str, None] = "e3b9c1d74a26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STEPS = "('restore', 'prepare', 'user_provision', 'kernel_change', 'mode_switch', 'reboot_verify')"
_NEW_STEPS = (
    "('restore', 'prepare', 'user_provision', 'pam_fix', 'stand_setup', "
    "'kernel_change', 'mode_switch', 'reboot_verify')"
)


def upgrade() -> None:
    op.add_column("server_prepare_for_test_requests", sa.Column("stand_setup", postgresql.JSONB(), nullable=True))
    op.add_column(
        "server_prepare_for_test_requests", sa.Column("stand_setup_script_encrypted", sa.Text(), nullable=True),
    )
    op.add_column("server_prepare_for_test_requests", sa.Column("provisioning", postgresql.JSONB(), nullable=True))
    op.drop_constraint("ck_prepare_for_test_failed_step", "server_prepare_for_test_requests", type_="check")
    op.create_check_constraint(
        "ck_prepare_for_test_failed_step", "server_prepare_for_test_requests",
        f"failed_step IS NULL OR failed_step IN {_NEW_STEPS}",
    )
    op.create_table(
        "server_stand_setup_requests",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "server_id", sa.String(length=64),
            sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("requested_by_department_id", sa.String(length=64), nullable=True),
        sa.Column("requested_by_service", sa.String(length=64), nullable=False),
        sa.Column("test_username", sa.String(length=128), nullable=False),
        sa.Column("stand_setup", postgresql.JSONB(), nullable=False),
        sa.Column("stand_setup_script_encrypted", sa.Text(), nullable=True),
        sa.Column("provisioning", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failed_step", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("callback_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("callback_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("callback_last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_server_stand_setup_requests_server_id", "server_stand_setup_requests", ["server_id"])


def downgrade() -> None:
    op.drop_index("ix_server_stand_setup_requests_server_id", table_name="server_stand_setup_requests")
    op.drop_table("server_stand_setup_requests")
    op.execute(
        "UPDATE server_prepare_for_test_requests SET failed_step = 'reboot_verify' "
        "WHERE failed_step IN ('pam_fix', 'stand_setup')"
    )
    op.drop_constraint("ck_prepare_for_test_failed_step", "server_prepare_for_test_requests", type_="check")
    op.create_check_constraint(
        "ck_prepare_for_test_failed_step", "server_prepare_for_test_requests",
        f"failed_step IS NULL OR failed_step IN {_OLD_STEPS}",
    )
    op.drop_column("server_prepare_for_test_requests", "provisioning")
    op.drop_column("server_prepare_for_test_requests", "stand_setup_script_encrypted")
    op.drop_column("server_prepare_for_test_requests", "stand_setup")
