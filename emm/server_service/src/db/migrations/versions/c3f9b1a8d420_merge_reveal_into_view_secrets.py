"""drop orphaned reveal_password / reveal_credentials grants

Revision ID: c3f9b1a8d420
Revises: b8d4e3f9a712
Create Date: 2026-05-27 12:00:00.000000

Reveal-эндпоинты (`server-accounts/{id}/reveal-password`,
`ipmi-controllers/{id}/reveal-credentials`) убраны: расшифрованный пароль
теперь приходит прямо в GET-карточке, если вызывающий держит `view_password`
(server_account) / `view_credentials` (ipmi_controller).

Action'ов `reveal_password` / `reveal_credentials` больше нет в
`core/constants.py::ENTITY_ACTIONS`, поэтому их seed-гранты осиротели —
снимаем. Сами `view_password` / `view_credentials` остаются как были
(baseline: только `admin`; worker_bot — отдельной миграцией). reader/guest
получают карточку без пароля; кому нужен plaintext — грант выдаётся явно
через `/permissions`.

Downgrade воссоздаёт reveal-гранты для admin/operator (состав из
`a2c1d8e4b9f5` и `b8d4e3f9a712`).
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "c3f9b1a8d420"
down_revision: Union[str, None] = "b8d4e3f9a712"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Снять reveal_* гранты — независимо от роли и department_id.
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE (entity_type = 'server_account' AND action = 'reveal_password') "
        "   OR (entity_type = 'ipmi_controller' AND action = 'reveal_credentials')"
    )


def downgrade() -> None:
    restored: list[tuple[str, str, str]] = [
        ("server_account", "admin", "reveal_password"),
        ("server_account", "operator", "reveal_password"),
        ("ipmi_controller", "admin", "reveal_credentials"),
        ("ipmi_controller", "operator", "reveal_credentials"),
    ]
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
            for entity_type, role, action in restored
        ],
    )
