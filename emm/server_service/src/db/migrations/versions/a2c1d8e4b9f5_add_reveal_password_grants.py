"""add server_account.reveal_password grants for admin/operator (merge heads)

Revision ID: a2c1d8e4b9f5
Revises: c8e4f6a9b1d2, a1b2c3d4e5f6
Create Date: 2026-05-22 06:00:00.000000

Новый user-facing action `(server_account, reveal_password)` — отдаёт
plaintext-пароль OS-аккаунта в base64 через `POST /server-accounts/{id}/
reveal-password`. Отделён от worker-only `view_password`, чтобы:

  * worker_bot не получал доступ к user-facing endpoint автоматически
    (его роль продолжает нести только `view_password`/`rotate_password`);
  * default-доступ ограничивался admin/operator-ролями в каждом
    департаменте. reader/guest остаются без grant'а.

Параллельно мёрджит две branch-головы (`c8e4f6a9b1d2` — drop installed
packages + cpu_models inline lineage, `a1b2c3d4e5f6` — remove service_role
entity), которые висели после параллельных правок матрицы. Сам merge
ничего не делает кроме declarative down_revision-tuple — реальные DDL
уже применены в своих миграциях.

Endpoint пишет audit `server_account.password_revealed` (severity WARNING)
на каждое раскрытие; на failure/denied — тот же action со status'ом
denied/failure (severity loging_service дефолтит выше через rules).

Downgrade удаляет только две seed-строки.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "a2c1d8e4b9f5"
down_revision: Union[str, Sequence[str], None] = ("c8e4f6a9b1d2", "a1b2c3d4e5f6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_GRANTS: list[tuple[str, str, str]] = [
    ("server_account", "admin", "reveal_password"),
    ("server_account", "operator", "reveal_password"),
]


def upgrade() -> None:
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": f"prm_{uuid4().hex}",
                "entity_type": entity_type,
                "role": role,
                "action": action,
            }
            for entity_type, role, action in _NEW_GRANTS
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'server_account' "
        "  AND action = 'reveal_password' "
        "  AND role IN ('admin', 'operator')"
    )
