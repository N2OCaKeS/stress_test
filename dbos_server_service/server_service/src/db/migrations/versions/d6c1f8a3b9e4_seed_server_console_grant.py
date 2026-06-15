"""seed (server, console) grant for admin and operator

Адресует WebSocket-эндпоинт `WS /api/server/v1/servers/{id}/console/ws`.
Пара `(server, console)` добавлена в `constants.ENTITY_ACTIONS[server]` —
этот seed подкатывает дефолтные гранты как делают 831ba55543e9 / a8d2b7c1e394.

* admin / operator — получают: интерактивная консоль это оперативное
  действие, держателю operator-роли оно нужно для рутинного обслуживания
  подготовленного сервера.
* reader — НЕ получает: только чтение карточек, без живого shell'а.
* worker_bot — НЕ получает: бот не открывает консоли, он только исполняет
  PTY-мост по сигналу server_service.

Revision ID: d6c1f8a3b9e4
Revises: c4d8e1f9a7b2
Create Date: 2026-06-15
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d6c1f8a3b9e4"
down_revision: Union[str, None] = "c4d8e1f9a7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("server", "admin", "console"),
    ("server", "operator", "console"),
]


def upgrade() -> None:
    for entity_type, role, action in _GRANTS:
        op.execute(
            sa.text(
                """
                INSERT INTO entity_permissions (id, entity_type, role, action, department_id)
                SELECT :id, :etype, :role, :action, NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM entity_permissions
                    WHERE entity_type = :etype
                      AND role = :role
                      AND action = :action
                      AND department_id IS NULL
                )
                """
            ).bindparams(
                id=f"prm_{uuid4().hex}",
                etype=entity_type,
                role=role,
                action=action,
            )
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'server' AND action = 'console'"
    )
