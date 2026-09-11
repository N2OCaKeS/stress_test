"""permission entity_type + admin seed for the permission matrix itself

Revision ID: a7f4c9e2b816
Revises: 9c3f7a2e5d81
Create Date: 2026-09-11 10:00:00.000000

Заводит write-side матрицы прав (`/permissions*`, `permission_service.py`):
до этой миграции единственный существующий `admin` мог видеть/менять чужие
domain-матрицы (global_variable/test_stand/...) только косвенно, потому что
CRUD самой матрицы не существовало вовсе. Теперь `entity_permissions`
разрешает `entity_type='permission'` с действиями `view`/`permission_grant`/
`permission_revoke` — сеется той же схемой, что и остальные домены (системная
роль `admin`, `department_id IS NULL`), иначе текущий единственный admin не
смог бы пользоваться новым grant/revoke API.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "a7f4c9e2b816"
down_revision: Union[str, None] = "9c3f7a2e5d81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PERMISSION_ACTIONS: list[str] = ["view", "permission_grant", "permission_revoke"]


def upgrade() -> None:
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
                "entity_type": "permission",
                "role": "admin",
                "action": action,
            }
            for action in _PERMISSION_ACTIONS
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM entity_permissions WHERE entity_type = 'permission'")
    )
