"""fix stands table

Revision ID: 955ab102e545
Revises: d9dc726e6bcd
Create Date: 2024-02-25 18:42:22.651722

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '955ab102e545'
down_revision: Union[str, None] = 'd9dc726e6bcd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
