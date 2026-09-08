"""seed admin view_test_credentials grant

Revision ID: c4e91a7f3d68
Revises: fbad9bc3b9dd
Create Date: 2026-09-08

Раскрытие учётки исполнения теста (`server_test_credentials`) человеку —
живая отладка стенда во время/после прогона (план ALLTA MIGRATION §5.3:
и админ server_service, и (позже, проксируя сюда) админ testing_service
должны иметь доступ). Один (entity, action) — `(server, view_test_credentials)`,
засеян только роли `admin` — в отличие от `view_management_credentials`
(worker-only) это обычный человеческий грант, per-department admin может
дальше расширить его на кастомную роль через `/permissions`, если решит.

Downgrade удаляет только эту строку.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "c4e91a7f3d68"
down_revision: Union[str, None] = "fbad9bc3b9dd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANT: tuple[str, str] = ("server", "view_test_credentials")


def upgrade() -> None:
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": _GRANT[0],
                "role": "admin",
                "action": _GRANT[1],
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE role = 'admin' "
        "AND entity_type = 'server' "
        "AND action = 'view_test_credentials'"
    )
