"""confluence credential id

Revision ID: 80c32bfd05d0
Revises: c4a8e1f7d9b6
Create Date: 2026-09-15 21:00:00.000000

C4 (`SERVICE_CREDENTIALS.md`): Confluence получает собственную ссылку на
credential в secret_service, отдельную от `credential_id` (Jira/Zephyr/Tempo).
Пусто — Confluence-публикации (`run_summary`, `stp_matrix`, `activity_report`)
падают обратно на `credential_id`, как и раньше.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "80c32bfd05d0"
down_revision: Union[str, None] = "c4a8e1f7d9b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "department_integration_settings",
        sa.Column("confluence_credential_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("department_integration_settings", "confluence_credential_id")
