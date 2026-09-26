"""Scenario as the launch mechanism of an STP test case; scenario runs linked to STP and campaigns

Revision ID: c3e8a1f5d207
Revises: a6d1f3b7c924
Create Date: 2026-09-25 12:00:00.000000

* `scenarios.stp_test_case_code` — тест-кейс СТП, который запускается этим
  сценарием (как `test_definitions.code` у одиночного теста). Уникален в
  отделе. Сценарии FreeIPA, заведённые `a6d1f3b7c924`, получают свой `code`.
* `scenario_runs.stp_test_run_id` — столбец СТП, в ячейку которого пишется
  вердикт; `test_run_entry_id` и FK на `test_run_id` — запуск из кампании.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e8a1f5d207"
down_revision: Union[str, None] = "a6d1f3b7c924"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FREEIPA_SCENARIOS = ("freeipa.auth", "freeipa.create_users", "freeipa.plugin")


def upgrade() -> None:
    op.add_column("scenarios", sa.Column("stp_test_case_code", sa.String(128), nullable=True))
    op.create_unique_constraint(
        "uq_scenarios_department_stp_case", "scenarios", ["department_id", "stp_test_case_code"],
    )
    op.get_bind().execute(sa.text(
        "UPDATE scenarios SET stp_test_case_code = code "
        "WHERE code = ANY(:codes) AND stp_test_case_code IS NULL"
    ), {"codes": list(FREEIPA_SCENARIOS)})

    op.add_column("scenario_runs", sa.Column("stp_test_run_id", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_scenario_runs_stp_test_run", "scenario_runs", "stp_test_runs",
        ["stp_test_run_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_scenario_runs_stp_test_run_id", "scenario_runs", ["stp_test_run_id"])

    op.execute(
        "UPDATE scenario_runs SET test_run_id = NULL WHERE test_run_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM test_runs t WHERE t.id = scenario_runs.test_run_id)"
    )
    op.create_foreign_key(
        "fk_scenario_runs_test_run", "scenario_runs", "test_runs",
        ["test_run_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_scenario_runs_test_run_id", "scenario_runs", ["test_run_id"])
    op.add_column("scenario_runs", sa.Column("test_run_entry_id", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_scenario_runs_test_run_entry", "scenario_runs", "test_run_entries",
        ["test_run_entry_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_scenario_runs_test_run_entry_id", "scenario_runs", ["test_run_entry_id"])


def downgrade() -> None:
    op.drop_index("ix_scenario_runs_test_run_entry_id", table_name="scenario_runs")
    op.drop_constraint("fk_scenario_runs_test_run_entry", "scenario_runs", type_="foreignkey")
    op.drop_column("scenario_runs", "test_run_entry_id")
    op.drop_index("ix_scenario_runs_test_run_id", table_name="scenario_runs")
    op.drop_constraint("fk_scenario_runs_test_run", "scenario_runs", type_="foreignkey")
    op.drop_index("ix_scenario_runs_stp_test_run_id", table_name="scenario_runs")
    op.drop_constraint("fk_scenario_runs_stp_test_run", "scenario_runs", type_="foreignkey")
    op.drop_column("scenario_runs", "stp_test_run_id")
    op.drop_constraint("uq_scenarios_department_stp_case", "scenarios", type_="unique")
    op.drop_column("scenarios", "stp_test_case_code")
