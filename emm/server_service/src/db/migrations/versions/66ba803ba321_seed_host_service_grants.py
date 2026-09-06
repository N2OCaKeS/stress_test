"""seed host_service manage/control grants for system admin role

Both host_service actions (manage/control) are granted to the system `admin`
role platform-wide (`department_id IS NULL`), same as every other system
`admin` seed — the department's own `admin` service-role holder gets these
automatically in every department, no per-department seeding needed.

Revision ID: 66ba803ba321
Revises: 8b2f9818fda7
Create Date: 2026-09-06 00:00:00.000000
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "66ba803ba321"
down_revision: Union[str, None] = "8b2f9818fda7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS = ("host_service_manage", "host_service_control")


def upgrade() -> None:
    for action in _ACTIONS:
        op.execute(
            sa.text(
                """
                INSERT INTO entity_permissions (id, entity_type, role, action, department_id)
                SELECT :id, 'host_service', 'admin', :action, NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM entity_permissions
                    WHERE entity_type = 'host_service'
                      AND role = 'admin'
                      AND action = :action
                      AND department_id IS NULL
                )
                """
            ).bindparams(id=f"prm_{uuid4().hex}", action=action)
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions WHERE entity_type = 'host_service' AND action = ANY(:actions)"
        ).bindparams(actions=list(_ACTIONS))
    )
