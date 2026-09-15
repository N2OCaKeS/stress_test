"""stp matrix confluence publication

Revision ID: 25224c748ae0
Revises: a8e30c12d564
Create Date: 2026-09-15 12:00:00.000000

Публикация сводной СТП-матрицы в Confluence (§D2/D3 плана миграции,
эталонные отчёты) — перенос легаси `ZefirResultTable`
(`allta_app/libs/zefir.py:203-436`) с одним сознательным изменением:
пространство Confluence и заголовок grandparent-страницы иерархии — per-
department настройки, а не платформенный хардкод `DEVQA`/`'Состав тестового
прогона'`.

* `department_integration_settings` донаполняется двумя полями:
  `stp_matrix_confluence_space`/`stp_matrix_confluence_root_page_title`.
* `stp_matrix_publications` — состояние публикации, одна строка на
  `(department_id, os_version_id)`, тот же паттерн, что `run_summary_comments`
  (`body_snapshot` — diff перед повторным `update_page`).

Права: `stp_test_run` получает новое действие `publish` (department-scoped,
`require_department_action`) — системная роль `admin`.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "25224c748ae0"
down_revision: Union[str, None] = "a8e30c12d564"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "department_integration_settings",
        sa.Column("stp_matrix_confluence_space", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column("stp_matrix_confluence_root_page_title", sa.String(length=256), nullable=True),
    )

    op.create_table(
        "stp_matrix_publications",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("os_version_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("confluence_page_id", sa.String(length=64), nullable=True),
        sa.Column("confluence_parent_page_id", sa.String(length=64), nullable=True),
        sa.Column("body_snapshot", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(length=64), nullable=True),
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
        "ix_stp_matrix_publications_department_id", "stp_matrix_publications", ["department_id"],
    )
    op.create_index(
        "ix_stp_matrix_publications_os_version_id", "stp_matrix_publications", ["os_version_id"],
    )
    op.create_unique_constraint(
        "uq_stp_matrix_publications_dept_osv",
        "stp_matrix_publications", ["department_id", "os_version_id"],
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
        [{
            "id": f"prm_{uuid4().hex}",
            "entity_type": "stp_test_run",
            "role": "admin",
            "action": "publish",
        }],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions WHERE entity_type = 'stp_test_run' AND action = 'publish'"
        )
    )
    op.drop_constraint("uq_stp_matrix_publications_dept_osv", "stp_matrix_publications", type_="unique")
    op.drop_index("ix_stp_matrix_publications_os_version_id", table_name="stp_matrix_publications")
    op.drop_index("ix_stp_matrix_publications_department_id", table_name="stp_matrix_publications")
    op.drop_table("stp_matrix_publications")
    op.drop_column("department_integration_settings", "stp_matrix_confluence_root_page_title")
    op.drop_column("department_integration_settings", "stp_matrix_confluence_space")
