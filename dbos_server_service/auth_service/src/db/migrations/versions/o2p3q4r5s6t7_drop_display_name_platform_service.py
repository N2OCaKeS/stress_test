"""drop PlatformService.display_name

Revision ID: o2p3q4r5s6t7
Revises: n1o2p3q4r5s6
Create Date: 2026-06-11 12:02:00.000000

У PlatformService identifier — `service_name` (technical enum, на нём висят
URL-ы, FK и audit-события). `display_name` сносим: UI показывает
`service_name` напрямую. Копирование не нужно — `service_name` уже корректен.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "o2p3q4r5s6t7"
down_revision: Union[str, None] = "n1o2p3q4r5s6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("platform_services", "display_name")


def downgrade() -> None:
    op.add_column(
        "platform_services",
        sa.Column("display_name", sa.String(length=256), nullable=True),
    )
    op.execute("UPDATE platform_services SET display_name = service_name")
    op.alter_column("platform_services", "display_name", nullable=False)
