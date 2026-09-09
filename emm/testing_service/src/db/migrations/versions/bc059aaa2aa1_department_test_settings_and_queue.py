"""department test settings + queue items

Revision ID: bc059aaa2aa1
Revises: 7b22409f4211
Create Date: 2026-09-09 19:30:00.000000

Четвёртый домен testing_service (§2.4, §5, §5.5 плана миграции):
`department_test_settings` (per-department retry/имя тестового пользователя)
и `queue_items` (очередь прогонов на стенде — оркестрация `prepare-for-test`,
retry, приём в `testing_worker`).

`stand_id`/`test_id` на `queue_items` — настоящие FK: `test_stands`/
`test_definitions` уже живут в этой БД (в отличие от `server_id`/
`os_version_id`, которые указывают в БД server_service). `ON DELETE
RESTRICT` — стенд/тест с историей прогонов удалить нельзя, только
деактивировать.

Права:
* `department_test_settings` — системная роль `admin`, действия
  `view`/`update` (upsert не заводит отдельного `create`).
* `test_stand` донаполняется действием `view_test_credentials` (§5.3) — та
  же роль `admin`, что и остальные действия этой сущности из предыдущей
  миграции.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bc059aaa2aa1"
down_revision: Union[str, None] = "7b22409f4211"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEPARTMENT_TEST_SETTINGS_ACTIONS: list[str] = ["view", "update"]


def upgrade() -> None:
    op.create_table(
        "department_test_settings",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("retry_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("test_username", sa.String(length=32), nullable=False, server_default="u"),
        sa.Column("activity_report_schedule", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_department_test_settings_department_id",
        "department_test_settings", ["department_id"], unique=True,
    )

    op.create_table(
        "queue_items",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "stand_id", sa.String(length=64),
            sa.ForeignKey("test_stands.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "test_id", sa.String(length=64),
            sa.ForeignKey("test_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "launch_context", postgresql.JSONB(),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_retry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "retry_of_id", sa.String(length=64),
            sa.ForeignKey("queue_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("debug_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prepare_request_id", sa.String(length=64), nullable=True),
        sa.Column("creds_stash_key", sa.String(length=128), nullable=True),
        sa.Column("failed_step", sa.String(length=32), nullable=True),
        sa.Column("error", sa.String(length=2048), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_queue_items_stand_id", "queue_items", ["stand_id"])
    op.create_index("ix_queue_items_test_id", "queue_items", ["test_id"])
    op.create_index("ix_queue_items_state", "queue_items", ["state"])
    op.create_index(
        "ix_queue_items_prepare_request_id", "queue_items", ["prepare_request_id"],
    )

    permissions = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "department_test_settings",
                "role": "admin",
                "action": action,
            }
            for action in _DEPARTMENT_TEST_SETTINGS_ACTIONS
        ]
        + [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "test_stand",
                "role": "admin",
                "action": "view_test_credentials",
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions WHERE entity_type = 'department_test_settings'"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions "
            "WHERE entity_type = 'test_stand' AND action = 'view_test_credentials'"
        )
    )
    op.drop_index("ix_queue_items_prepare_request_id", table_name="queue_items")
    op.drop_index("ix_queue_items_state", table_name="queue_items")
    op.drop_index("ix_queue_items_test_id", table_name="queue_items")
    op.drop_index("ix_queue_items_stand_id", table_name="queue_items")
    op.drop_table("queue_items")
    op.drop_index(
        "ix_department_test_settings_department_id", table_name="department_test_settings",
    )
    op.drop_table("department_test_settings")
