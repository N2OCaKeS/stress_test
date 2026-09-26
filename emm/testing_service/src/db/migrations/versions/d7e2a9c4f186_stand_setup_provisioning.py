"""Test stand setup step, provisioning profiles

Revision ID: d7e2a9c4f186
Revises: c1f7a3e9d402
Create Date: 2026-09-24 00:00:00.000000

`test_definitions.stand_setup` — шаг настройки стенда теста
(доп. параметры ядра, bash-скрипт, фаза, перезагрузка). Легаси для
`postgresql-aud-off` и `parsec impact-fs aud-off` дописывало `audit=0` в
`GRUB_CMDLINE_LINUX_DEFAULT` (`emm/allta_app_full/backup_image.py:715-716`,
флаги `args.AUDIT_OFF`/`args.PARSEC_IMPACT_AO`) — у тестов
`postgresql.audit_off` и `parsec.impact_fs_audit_off` сидится
`kernel_cmdline_extra = ["audit=0"]` (если шаг ещё не задан руками).

`provisioning_profiles` + `test_definitions.provisioning_profile_id`.
Общий профиль по умолчанию — легаси `socket_available()`
(`backup_image.py:554-600`): `degraded` только из-за
`astra-mount-lock.service` — готово, иначе перезагрузка до 3 раз; PAM —
`pam_lastlog.so inactive=` комментируется всегда (`backup_image.py:807-808`).

Права `provisioning_profile:view/update` — системной роли `admin` (как у
профиля запуска); department_admin своего отдела проходит мимо матрицы.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7e2a9c4f186"
down_revision: Union[str, None] = "c1f7a3e9d402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUDIT_OFF_TESTS = ("postgresql.audit_off", "parsec.impact_fs_audit_off")


def upgrade() -> None:
    op.create_table(
        "provisioning_profiles",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("allowed_failed_units", postgresql.JSONB(), nullable=False),
        sa.Column("degraded_reboot_attempts", sa.Integer(), nullable=False),
        sa.Column("disable_pam_lastlog_inactive", sa.Boolean(), nullable=False),
        sa.Column("boot_wait_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_provisioning_profiles_department_id", "provisioning_profiles", ["department_id"])
    op.create_index(
        "uq_provisioning_profiles_default", "provisioning_profiles", [sa.text("coalesce(department_id, '')")],
        unique=True, postgresql_where=sa.text("is_default"),
    )
    profiles = sa.table(
        "provisioning_profiles",
        sa.column("id", sa.String), sa.column("department_id", sa.String), sa.column("name", sa.String),
        sa.column("is_default", sa.Boolean), sa.column("allowed_failed_units", postgresql.JSONB),
        sa.column("degraded_reboot_attempts", sa.Integer), sa.column("disable_pam_lastlog_inactive", sa.Boolean),
    )
    op.bulk_insert(profiles, [{
        "id": "pp_default", "department_id": None, "name": "Легаси", "is_default": True,
        "allowed_failed_units": ["astra-mount-lock.service"], "degraded_reboot_attempts": 3,
        "disable_pam_lastlog_inactive": True,
    }])

    op.add_column("test_definitions", sa.Column("stand_setup", postgresql.JSONB(), nullable=True))
    op.add_column("test_definitions", sa.Column(
        "provisioning_profile_id", sa.String(length=64),
        sa.ForeignKey("provisioning_profiles.id", ondelete="SET NULL", name="fk_test_definitions_provisioning_profile"),
        nullable=True,
    ))
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE test_definitions SET stand_setup = CAST(:setup AS jsonb), updated_at = now() "
            "WHERE code IN :codes AND stand_setup IS NULL"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {
            "codes": list(_AUDIT_OFF_TESTS),
            "setup": '{"kernel_cmdline_extra": ["audit=0"], "script": "", "run_as": "root", '
                     '"phase": "after_boot", "reboot_after": null, "timeout_seconds": 1800}',
        },
    )

    permissions = sa.table(
        "entity_permissions",
        sa.column("id", sa.String), sa.column("entity_type", sa.String),
        sa.column("role", sa.String), sa.column("action", sa.String),
    )
    op.bulk_insert(permissions, [
        {"id": f"prm_{uuid4().hex}", "entity_type": "provisioning_profile", "role": "admin", "action": action}
        for action in ("view", "update")
    ])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM entity_permissions WHERE entity_type = 'provisioning_profile'"))
    op.drop_constraint("fk_test_definitions_provisioning_profile", "test_definitions", type_="foreignkey")
    op.drop_column("test_definitions", "provisioning_profile_id")
    op.drop_column("test_definitions", "stand_setup")
    op.drop_index("uq_provisioning_profiles_default", table_name="provisioning_profiles")
    op.drop_index("ix_provisioning_profiles_department_id", table_name="provisioning_profiles")
    op.drop_table("provisioning_profiles")
