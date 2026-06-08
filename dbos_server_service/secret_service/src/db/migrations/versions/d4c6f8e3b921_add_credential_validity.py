"""add valid_from / valid_to to credentials

Поля валидности секрета — для токенов с конечным сроком жизни (Jira /
Confluence PAT'ы ~90 дней). Reveal вне окна `[valid_from, valid_to]` → 410
GONE с error_code SECRET_NOT_YET_VALID / SECRET_EXPIRED. Метаданные (GET без
reveal) остаются доступными независимо от срока — иначе UI не сможет показать
"продлите токен".

CHECK гарантирует `valid_to > valid_from` если оба заданы; одиночные значения
разрешены (open-ended окно с одной стороны).

Revision ID: d4c6f8e3b921
Revises: c3b5e7d2a1f8
Create Date: 2026-06-09 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4c6f8e3b921"
down_revision: Union[str, None] = "c3b5e7d2a1f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.null(),
        ),
    )
    op.add_column(
        "credentials",
        sa.Column(
            "valid_to",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.null(),
        ),
    )
    op.create_check_constraint(
        "ck_credentials_validity_window",
        "credentials",
        "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_credentials_validity_window", "credentials", type_="check"
    )
    op.drop_column("credentials", "valid_to")
    op.drop_column("credentials", "valid_from")
