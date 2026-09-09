"""stp zephyr integration

Revision ID: e7c2a49f18b6
Revises: a3f6c8e91d47
Create Date: 2026-09-10 15:00:00.000000

Седьмой домен testing_service (§2.5, §6, §7 плана миграции, волна 8): СТП
(состав тестового прогона) + интеграция с Jira Zephyr Scale ATM + Confluence
+ changelog-фильтрация.

Новые таблицы:

* `stp_test_cases` — зеркало Zephyr Scale test-case (`code` — join-ключ с
  `test_definitions.code`, UNIQUE).
* `stp_test_runs` — Zephyr test-run/execution, один на стенд на вызов
  `/stp/generate`. `stand_id` — настоящий FK на `test_stands` (та же БД).
* `stp_cells` — статус `(stp_test_case × stp_test_run)`, `UNIQUE` на пару.
  `queue_item_id` (FK, SET NULL) — статус пришёл автоматически;
  `updated_by` (soft-ref, без FK) — статус выставлен вручную. Ровно одно из
  двух заполнено — держит application code, не DB CHECK.
* `department_integration_settings` — per-department `credential_id`
  (id credential в secret_service, без FK) + `jira_base_url` +
  `confluence_base_url`.

`test_definitions.changelog_component` — новая nullable-колонка, используется
ТОЛЬКО changelog-фильтром генерации СТП (§1/§7); пусто — тест считается
затронутым любым changelog (безопасный дефолт).

Права: системная роль `admin` — `stp_test_case` (`view`/`create`/`update`/
`delete`), `stp_test_run` (`create` — генерация), `stp_cell` (`update` —
ручной override, событийное обновление из очереди в матрицу не ходит),
`department_integration_settings` (`view`/`update`) — тот же паттерн, что у
`department_test_settings`/`global_variable`.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "e7c2a49f18b6"
down_revision: Union[str, None] = "a3f6c8e91d47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_STP_TEST_CASE_ACTIONS = ["view", "create", "update", "delete"]
_DEPARTMENT_INTEGRATION_SETTINGS_ACTIONS = ["view", "update"]


def upgrade() -> None:
    op.add_column(
        "test_definitions",
        sa.Column("changelog_component", sa.String(length=128), nullable=True),
    )

    op.create_table(
        "stp_test_cases",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("zephyr_id", sa.String(length=32), nullable=True),
        sa.Column("department_id", sa.String(length=64), nullable=True),
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
    op.create_index("ix_stp_test_cases_code", "stp_test_cases", ["code"], unique=True)
    op.create_index("ix_stp_test_cases_department_id", "stp_test_cases", ["department_id"])

    op.create_table(
        "stp_test_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("os_version_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("kernel", sa.String(length=64), nullable=False),
        sa.Column(
            "stand_id", sa.String(length=64),
            sa.ForeignKey("test_stands.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("zephyr_test_run_key", sa.String(length=32), nullable=True),
        sa.Column("zephyr_folder_path", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_stp_test_runs_os_version_id", "stp_test_runs", ["os_version_id"])
    op.create_index("ix_stp_test_runs_stand_id", "stp_test_runs", ["stand_id"])

    op.create_table(
        "stp_cells",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "stp_test_case_id", sa.String(length=64),
            sa.ForeignKey("stp_test_cases.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "stp_test_run_id", sa.String(length=64),
            sa.ForeignKey("stp_test_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="not_run"),
        sa.Column(
            "queue_item_id", sa.String(length=64),
            sa.ForeignKey("queue_items.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_stp_cells_stp_test_case_id", "stp_cells", ["stp_test_case_id"])
    op.create_index("ix_stp_cells_stp_test_run_id", "stp_cells", ["stp_test_run_id"])
    op.create_unique_constraint(
        "uq_stp_cells_case_run", "stp_cells", ["stp_test_case_id", "stp_test_run_id"],
    )

    op.create_table(
        "department_integration_settings",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.String(length=64), nullable=True),
        sa.Column("jira_base_url", sa.String(length=256), nullable=True),
        sa.Column("confluence_base_url", sa.String(length=256), nullable=True),
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
        "ix_department_integration_settings_department_id",
        "department_integration_settings", ["department_id"], unique=True,
    )

    permissions = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    rows = [
        {"id": f"prm_{uuid4().hex}", "entity_type": "stp_test_case", "role": "admin", "action": action}
        for action in _STP_TEST_CASE_ACTIONS
    ]
    rows.append({"id": f"prm_{uuid4().hex}", "entity_type": "stp_test_run", "role": "admin", "action": "create"})
    rows.append({"id": f"prm_{uuid4().hex}", "entity_type": "stp_cell", "role": "admin", "action": "update"})
    rows.extend([
        {
            "id": f"prm_{uuid4().hex}",
            "entity_type": "department_integration_settings",
            "role": "admin",
            "action": action,
        }
        for action in _DEPARTMENT_INTEGRATION_SETTINGS_ACTIONS
    ])
    op.bulk_insert(permissions, rows)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions WHERE entity_type IN "
            "('stp_test_case', 'stp_test_run', 'stp_cell', 'department_integration_settings')"
        )
    )
    op.drop_index(
        "ix_department_integration_settings_department_id",
        table_name="department_integration_settings",
    )
    op.drop_table("department_integration_settings")

    op.drop_constraint("uq_stp_cells_case_run", "stp_cells", type_="unique")
    op.drop_index("ix_stp_cells_stp_test_run_id", table_name="stp_cells")
    op.drop_index("ix_stp_cells_stp_test_case_id", table_name="stp_cells")
    op.drop_table("stp_cells")

    op.drop_index("ix_stp_test_runs_stand_id", table_name="stp_test_runs")
    op.drop_index("ix_stp_test_runs_os_version_id", table_name="stp_test_runs")
    op.drop_table("stp_test_runs")

    op.drop_index("ix_stp_test_cases_department_id", table_name="stp_test_cases")
    op.drop_index("ix_stp_test_cases_code", table_name="stp_test_cases")
    op.drop_table("stp_test_cases")

    op.drop_column("test_definitions", "changelog_component")
