"""consolidate acs_snapshot_list/create/restore into one action

Три отдельных action'а (`acs_snapshot_list`/`_create`/`_restore`) сведены в
один `acs_snapshot` — держатель права получает список+создание+восстановление
разом. Восстановление внутри необратимо перезаписывает диск, поэтому весь
action целиком остаётся тип-wide (не грантуется точечно на один сервер).

Revision ID: 68c541e6a518
Revises: d2448cb78942
Create Date: 2026-09-03
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "68c541e6a518"
down_revision: Union[str, None] = "d2448cb78942"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_ACTIONS = ("acs_snapshot_list", "acs_snapshot_create", "acs_snapshot_restore")
_NEW_ACTION = "acs_snapshot"


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions "
            "WHERE entity_type = 'server' AND action = ANY(:actions)"
        ).bindparams(actions=list(_OLD_ACTIONS))
    )
    op.execute(
        sa.text(
            """
            INSERT INTO entity_permissions (id, entity_type, role, action, department_id)
            SELECT :id, 'server', 'admin', :action, NULL
            WHERE NOT EXISTS (
                SELECT 1 FROM entity_permissions
                WHERE entity_type = 'server'
                  AND role = 'admin'
                  AND action = :action
                  AND department_id IS NULL
            )
            """
        ).bindparams(id=f"prm_{uuid4().hex}", action=_NEW_ACTION)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM entity_permissions WHERE entity_type = 'server' AND action = :action"
        ).bindparams(action=_NEW_ACTION)
    )
    for action in _OLD_ACTIONS:
        op.execute(
            sa.text(
                """
                INSERT INTO entity_permissions (id, entity_type, role, action, department_id)
                SELECT :id, 'server', 'admin', :action, NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM entity_permissions
                    WHERE entity_type = 'server'
                      AND role = 'admin'
                      AND action = :action
                      AND department_id IS NULL
                )
                """
            ).bindparams(id=f"prm_{uuid4().hex}", action=action)
        )
