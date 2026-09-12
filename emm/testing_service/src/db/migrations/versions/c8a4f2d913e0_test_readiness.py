"""Constrain test readiness and migrate legacy statuses."""

from alembic import op
import sqlalchemy as sa

revision = "c8a4f2d913e0"
down_revision = "b6d2f9a1c735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE test_definitions SET readiness = CASE
            WHEN readiness = 'ready' THEN 'ready'
            WHEN readiness IN ('draft', 'development') THEN 'development'
            WHEN readiness IN ('blocked', 'broken') THEN 'broken'
            ELSE 'review'
        END
    """)
    op.alter_column(
        "test_definitions",
        "readiness",
        existing_type=sa.String(32),
        nullable=False,
        server_default="development",
    )
    op.create_check_constraint(
        "ck_test_definitions_readiness",
        "test_definitions",
        "readiness IN ('ready', 'review', 'broken', 'development')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_test_definitions_readiness", "test_definitions", type_="check"
    )
    op.alter_column(
        "test_definitions",
        "readiness",
        existing_type=sa.String(32),
        nullable=True,
        server_default=None,
    )
    op.execute("""
        UPDATE test_definitions SET readiness = CASE
            WHEN readiness = 'development' THEN 'draft'
            WHEN readiness = 'broken' THEN 'blocked'
            ELSE readiness
        END
    """)
