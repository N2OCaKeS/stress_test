"""add revoked_reason + revoked_at index to personal_access_tokens

Revision ID: c7d8e9f0a1b2
Revises: b5c6d7e8f9a0
Create Date: 2026-05-20 18:40:01.000000

Разделяем «ban-revoked PAT» от «user-initiated revoked PAT». До этой
колонки `unban_user` не мог реактивировать PAT, отозванные при ban'е,
потому что в БД не было способа отличить их от revoke'нутых самим
юзером через DELETE /tokens/{id}.

Колонка nullable + без default'а: legacy rows и user-initiated revoke
оставляют `revoked_reason=NULL`. Ban-revoke после этой миграции пишет
`revoked_reason="ban"`. `unban_user` ищет именно эти строки для
реактивации.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `revoked_reason`: nullable VARCHAR(32). Достаточно для значений
    # "ban" / "user" / "expired" / "admin_reset" / future use cases.
    op.add_column(
        "personal_access_tokens",
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
    )
    # Индекс на (user_id, revoked_reason) — unban_user сканирует именно по
    # этим двум полям, чтобы найти ban-revoked PAT юзера. Без индекса на
    # большой `personal_access_tokens` таблице (>10K строк per active user
    # base) выборка стала бы full scan.
    op.create_index(
        "ix_pat_user_revoked_reason",
        "personal_access_tokens",
        ["user_id", "revoked_reason"],
    )


def downgrade() -> None:
    op.drop_index("ix_pat_user_revoked_reason", table_name="personal_access_tokens")
    op.drop_column("personal_access_tokens", "revoked_reason")
