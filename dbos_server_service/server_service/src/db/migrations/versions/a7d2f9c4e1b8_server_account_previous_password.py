"""retain previous server_account password during rotation transition

Смена пароля учётки больше не должна сразу обновлять пароли на серверах.
Чтобы оператор мог подключаться и старым, и новым паролем, пока новый не
раскатан на все привязанные серверы, при ротации текущий ciphertext
переезжает в `previous_password_encrypted` (+ timestamp), а новый пишется в
`password_encrypted`. Прежний пароль зануляется, когда переходный период
закончен (первый provision-callback снял `credentials_pending_apply`).

Колонки nullable без default — у существующих строк переходного периода нет.

Revision ID: a7d2f9c4e1b8
Revises: f4a9c1e7b2d3
Create Date: 2026-06-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7d2f9c4e1b8"
down_revision: Union[str, None] = "f4a9c1e7b2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "server_accounts",
        sa.Column("previous_password_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "server_accounts",
        sa.Column(
            "previous_password_rotated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("server_accounts", "previous_password_rotated_at")
    op.drop_column("server_accounts", "previous_password_encrypted")
