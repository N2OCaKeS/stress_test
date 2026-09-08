"""prepare-for-test: состояние запроса + учётка исполнения теста

Revision ID: b3d7f6a1c852
Revises: a7c93f4b2e18
Create Date: 2026-09-08 15:40:00.000000

Две таблицы под асинхронный контракт `POST /internal/servers/{id}/
prepare-for-test` (вызывает testing_service, ответ 202 + callback):

  * `server_prepare_for_test_requests` — состояние пайплайна. Он идёт часами
    и обязан пережить рестарт сервиса; `correlation_id` уникален и держит
    идемпотентность (повтор не запускает второй restore на тот же диск),
    частичный уникальный индекс по `server_id` — «не больше одного активного
    пайплайна на стенд».
  * `server_test_credentials` — пароль и приватный ключ учётки, под которой
    реально исполняется тест. Тот же AES-256-GCM конверт и тот же
    length-cap 8192, что у `server_accounts`; одна строка на сервер,
    перевыпускается на каждую подготовку.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3d7f6a1c852"
down_revision: Union[str, None] = "d5f2a91c6e37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "server_prepare_for_test_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("server_id", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("os_version_id", sa.String(length=64), nullable=False),
        sa.Column("kernel", sa.String(length=128), nullable=False),
        sa.Column("test_username", sa.String(length=128), nullable=False),
        sa.Column("requested_by_department_id", sa.String(length=64), nullable=True),
        sa.Column("requested_by_service", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("failed_step", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "reservation_acquired",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("restore_task_id", sa.String(length=64), nullable=True),
        sa.Column("prepare_task_id", sa.String(length=64), nullable=True),
        sa.Column("provision_task_id", sa.String(length=64), nullable=True),
        sa.Column(
            "callback_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "callback_delivered_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("callback_last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "correlation_id", name="uq_prepare_for_test_correlation_id"
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'succeeded', 'failed')",
            name="ck_prepare_for_test_status",
        ),
        sa.CheckConstraint(
            "failed_step IS NULL OR failed_step IN "
            "('restore', 'prepare', 'user_provision', 'kernel_change', "
            "'reboot_verify')",
            name="ck_prepare_for_test_failed_step",
        ),
    )
    op.create_index(
        op.f("ix_server_prepare_for_test_requests_server_id"),
        "server_prepare_for_test_requests",
        ["server_id"],
    )
    op.create_index(
        "uq_prepare_for_test_active_server",
        "server_prepare_for_test_requests",
        ["server_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
    )

    op.create_table(
        "server_test_credentials",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("server_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("ssh_public_key", sa.Text(), nullable=False),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "rotated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", name="uq_server_test_credentials_server"),
        sa.CheckConstraint(
            "length(password_encrypted) < 8192",
            name="ck_server_test_credentials_password_len",
        ),
        sa.CheckConstraint(
            "length(ssh_private_key_encrypted) < 8192",
            name="ck_server_test_credentials_ssh_key_len",
        ),
    )


def downgrade() -> None:
    op.drop_table("server_test_credentials")
    op.drop_index(
        "uq_prepare_for_test_active_server",
        table_name="server_prepare_for_test_requests",
    )
    op.drop_index(
        op.f("ix_server_prepare_for_test_requests_server_id"),
        table_name="server_prepare_for_test_requests",
    )
    op.drop_table("server_prepare_for_test_requests")
