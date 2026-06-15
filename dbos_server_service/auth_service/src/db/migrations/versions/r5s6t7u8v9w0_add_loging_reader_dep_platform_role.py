"""add loging_reader_dep to users.platform_role CHECK

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
Create Date: 2026-06-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "r5s6t7u8v9w0"
down_revision: Union[str, None] = "q4r5s6t7u8v9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Зеркалит `core.constants.PlatformRole`. CHECK не PostgreSQL ENUM, поэтому
# добавление значения — drop + recreate constraint'а (ALTER TYPE не нужен).
_ALLOWED = (
    "account_admin",
    "department_admin",
    "loging_admin",
    "loging_reader",
    "loging_reader_dep",
)


def upgrade() -> None:
    values = ", ".join(f"'{v}'" for v in _ALLOWED)
    op.drop_constraint("ck_users_platform_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_platform_role",
        "users",
        f"platform_role IS NULL OR platform_role IN ({values})",
    )


def downgrade() -> None:
    prev = ("account_admin", "department_admin", "loging_admin", "loging_reader")
    values = ", ".join(f"'{v}'" for v in prev)
    op.drop_constraint("ck_users_platform_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_platform_role",
        "users",
        f"platform_role IS NULL OR platform_role IN ({values})",
    )
