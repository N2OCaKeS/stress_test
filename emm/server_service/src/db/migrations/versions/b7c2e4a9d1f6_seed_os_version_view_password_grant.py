"""seed os_version.view_password grant

Revision ID: b7c2e4a9d1f6
Revises: a4b8f2d1c6e9
Create Date: 2026-09-04 12:00:00.000000

`os_version.view_password` раскрывает bootstrap-пароль версии ОС через
GET /os-versions/{id}/bootstrap-password?reveal=true.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "b7c2e4a9d1f6"
down_revision: Union[str, Sequence[str], None] = "a4b8f2d1c6e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO entity_permissions (id, entity_type, role, action)
        SELECT 'prm_osv_view_password_admin', 'os_version', 'admin', 'view_password'
        WHERE NOT EXISTS (
            SELECT 1 FROM entity_permissions
            WHERE entity_type = 'os_version'
              AND role = 'admin'
              AND action = 'view_password'
              AND department_id IS NULL
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'os_version' "
        "  AND role = 'admin' "
        "  AND action = 'view_password'"
    )
