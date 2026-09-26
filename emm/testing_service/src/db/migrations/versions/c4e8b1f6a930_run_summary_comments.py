"""run summary comments

Revision ID: c4e8b1f6a930
Revises: f3a1d8c6b4e2
Create Date: 2026-09-10 17:00:00.000000

Девятый домен testing_service (§2.7, §9.2 плана миграции):
end-of-run идемпотентный комментарий в Confluence-блоге — перенос легаси
`SendCommentToConfluence`. Одна строка на `test_run_id`, `confluence_comment_id`
хранится для UPDATE при повторном прогоне того же RC (не только для проверки
"есть/нет", как в легаси).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8b1f6a930"
down_revision: Union[str, None] = "f3a1d8c6b4e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_summary_comments",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "test_run_id", sa.String(length=64),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("confluence_blog_id", sa.String(length=64), nullable=True),
        sa.Column("confluence_comment_id", sa.String(length=64), nullable=True),
        sa.Column("stp_page_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("body_snapshot", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_run_summary_comments_test_run_id",
        "run_summary_comments", ["test_run_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_run_summary_comments_test_run_id", table_name="run_summary_comments")
    op.drop_table("run_summary_comments")
