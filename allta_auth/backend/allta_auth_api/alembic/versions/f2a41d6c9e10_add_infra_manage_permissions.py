"""add infra management permissions

Revision ID: f2a41d6c9e10
Revises: 8d3a7c4f1b22
Create Date: 2026-02-13 22:40:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f2a41d6c9e10"
down_revision: Union[str, Sequence[str], None] = "8d3a7c4f1b22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (code, description)
        VALUES
            ('server.manage', 'Can manage servers'),
            ('vm.manage', 'Can manage virtual machines')
        ON CONFLICT (code) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO groups (name, description)
        VALUES ('infra_managers', 'Users allowed to manage servers and VMs')
        ON CONFLICT (name) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO group_permissions (group_id, permission_id)
        SELECT g.id, p.id
        FROM groups g
        JOIN permissions p ON p.code IN ('server.manage', 'vm.manage')
        WHERE g.name = 'infra_managers'
        ON CONFLICT DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code IN ('server.manage', 'vm.manage')
        WHERE r.name = 'admin'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ('server.manage', 'vm.manage')
        );
        """
    )
    op.execute(
        """
        DELETE FROM group_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ('server.manage', 'vm.manage')
        );
        """
    )
    op.execute("DELETE FROM groups WHERE name = 'infra_managers';")
    op.execute("DELETE FROM permissions WHERE code IN ('server.manage', 'vm.manage');")
