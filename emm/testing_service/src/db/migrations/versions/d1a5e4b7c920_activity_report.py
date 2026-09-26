"""department activity report (HR report)

Revision ID: d1a5e4b7c920
Revises: c4e8b1f6a930
Create Date: 2026-09-10 18:30:00.000000

Десятый домен testing_service (§2.7, §9.1 плана миграции):
HR/продуктивити-отчёт по активности отдела — перенос легаси
`allta_app/reports/departament_reports/libreport.py`.

* `department_integration_settings` донаполняется полями Bitbucket/Tempo/
  Confluence-report — те же учётки/URL'ы, что уже используются §3.5/§6/§9.2,
  плюс отдельный `bitbucket_credential_id` (Bitbucket может сидеть на другой
  сервисной учётке, чем Jira/Confluence этого же инстанса).
* `department_report_members` — редактируемый через API список сотрудников
  отдела вместо легаси-хардкода трёх словарей в коде.
* `department_activity_reports` — история попыток генерации отчёта (одна
  строка на попытку, не upsert по period — полезно видеть, что генерация уже
  когда-то падала и была повторена).
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d1a5e4b7c920"
down_revision: Union[str, None] = "c4e8b1f6a930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEPARTMENT_REPORT_MEMBER_ACTIONS: list[str] = ["view", "create", "update", "delete"]
_DEPARTMENT_ACTIVITY_REPORT_ACTIONS: list[str] = ["view", "create"]


def upgrade() -> None:
    op.add_column(
        "department_integration_settings",
        sa.Column("bitbucket_base_url", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column("bitbucket_project_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column("bitbucket_repo_slug", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column("bitbucket_credential_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column("jira_board_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column("tempo_team_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column("confluence_report_page_space", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column("confluence_report_parent_page_title", sa.String(length=256), nullable=True),
    )

    op.create_table(
        "department_report_members",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("bitbucket_username", sa.String(length=128), nullable=True),
        sa.Column("jira_author_name", sa.String(length=128), nullable=True),
        sa.Column("jira_tempo_worker_key", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
    op.create_index(
        "ix_department_report_members_department_id",
        "department_report_members", ["department_id"],
    )

    op.create_table(
        "department_activity_reports",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("generated_by", sa.String(length=64), nullable=True),
        sa.Column("confluence_page_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=1024), nullable=True),
    )
    op.create_index(
        "ix_department_activity_reports_department_id",
        "department_activity_reports", ["department_id"],
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
                "entity_type": "department_report_member",
                "role": "admin",
                "action": action,
            }
            for action in _DEPARTMENT_REPORT_MEMBER_ACTIONS
        ]
        + [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": "department_activity_report",
                "role": "admin",
                "action": action,
            }
            for action in _DEPARTMENT_ACTIVITY_REPORT_ACTIONS
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions WHERE entity_type IN "
            "('department_report_member', 'department_activity_report')"
        )
    )
    op.drop_index(
        "ix_department_activity_reports_department_id", table_name="department_activity_reports",
    )
    op.drop_table("department_activity_reports")
    op.drop_index(
        "ix_department_report_members_department_id", table_name="department_report_members",
    )
    op.drop_table("department_report_members")
    op.drop_column("department_integration_settings", "confluence_report_parent_page_title")
    op.drop_column("department_integration_settings", "confluence_report_page_space")
    op.drop_column("department_integration_settings", "tempo_team_id")
    op.drop_column("department_integration_settings", "jira_board_id")
    op.drop_column("department_integration_settings", "bitbucket_credential_id")
    op.drop_column("department_integration_settings", "bitbucket_repo_slug")
    op.drop_column("department_integration_settings", "bitbucket_project_key")
    op.drop_column("department_integration_settings", "bitbucket_base_url")
