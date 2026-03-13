"""merge auth alembic heads

Revision ID: e3b1a7d4c2f0
Revises: 1a5d2ce0b4aa, 9f2c6e1b8d44
Create Date: 2026-03-10 21:20:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "e3b1a7d4c2f0"
down_revision: Union[str, Sequence[str], None] = ("1a5d2ce0b4aa", "9f2c6e1b8d44")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Merge revision: schema/data changes are already applied in parent heads.
    pass


def downgrade() -> None:
    # Split merge head back into two branches.
    pass

