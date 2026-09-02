"""merge heads: docker_registry + service_roles/groups branches

Revision ID: e1f2a3b4c5d6
Revises: 8c217337ea4a, d7e8f9a0b1c2
Create Date: 2026-04-21 00:00:00.000000

"""
from typing import Sequence, Union

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, tuple] = ("8c217337ea4a", "d7e8f9a0b1c2")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
