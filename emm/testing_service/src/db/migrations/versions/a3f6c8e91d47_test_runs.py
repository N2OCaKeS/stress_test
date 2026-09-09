"""test runs

Revision ID: a3f6c8e91d47
Revises: 784536fa92db
Create Date: 2026-09-10 12:00:00.000000

Шестой домен testing_service (§2.4, §6.1 плана миграции, волна 7):
прогоны — fleet-wide кампания, ставящая в очередь весь пул закреплённых за
выбранными стендами тестов под один РЦ+ядро+режим.

`test_runs.os_version_id`/`kernel` — без FK, межсервисная/снэпшот-ссылка тем
же приёмом, что и у `global_variable`/`test_stand`/`test_log`. `test_run_
stands` — JSONB-снэпшот пула стендов, выбранного при создании (вход запроса,
не пересчитывается).

`queue_items.test_run_id` — новая колонка на уже существующей таблице:
связывает элемент очереди с породившей его кампанией, `NULL` для одиночных
`enqueue()`-вызовов вне кампании (как это работает сегодня). `ON DELETE
SET NULL`, не `RESTRICT`/`CASCADE` — удаления `test_runs` в сервисе пока нет,
но история отдельных item'ов очереди не должна схлопнуться каскадом вместе
с кампанией, если такая операция появится.

Права: системная роль `admin`, единственное действие `create` — чтение
(список/карточка) открыто любому аутентифицированному актору, как у
`test_definition`/`test_stand`.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3f6c8e91d47"
down_revision: Union[str, None] = "784536fa92db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("os_version_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("kernel", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column(
            "test_run_stands", postgresql.JSONB(),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_test_runs_os_version_id", "test_runs", ["os_version_id"])
    op.create_index("ix_test_runs_department_id", "test_runs", ["department_id"])
    op.create_index("ix_test_runs_status", "test_runs", ["status"])

    op.add_column(
        "queue_items",
        sa.Column(
            "test_run_id", sa.String(length=64),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_queue_items_test_run_id", "queue_items", ["test_run_id"])

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
                "entity_type": "test_run",
                "role": "admin",
                "action": "create",
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM entity_permissions WHERE entity_type = 'test_run'")
    )
    op.drop_index("ix_queue_items_test_run_id", table_name="queue_items")
    op.drop_column("queue_items", "test_run_id")
    op.drop_index("ix_test_runs_status", table_name="test_runs")
    op.drop_index("ix_test_runs_department_id", table_name="test_runs")
    op.drop_index("ix_test_runs_os_version_id", table_name="test_runs")
    op.drop_table("test_runs")
