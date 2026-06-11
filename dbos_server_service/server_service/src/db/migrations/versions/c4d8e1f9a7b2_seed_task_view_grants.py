"""seed (task, view) grants for reader/operator/admin

Адресует read-endpoint'ы `GET /api/server/v1/tasks` и
`GET /api/server/v1/tasks/{id}`. Пара `(task, view)` добавлена в
`constants.ENTITY_ACTIONS[task]`; этот seed подкатывает дефолтные гранты по
тому же образцу, что и a8d2b7c1e394 для `(task, cancel)`.

* reader/operator/admin — получают `(task, view)`: чтение истории task'ов
  доступно всем трём системным ролям. Гранта `(task, cancel)` этот seed не
  трогает — cancel выдан отдельной миграцией a8d2b7c1e394 и только admin'у
  (reader/operator его НЕ получают).
* worker_bot — НЕ получает: воркер сам ничего не листает, он только мутирует
  свои row'ы.

Revision ID: c4d8e1f9a7b2
Revises: f8e9d0c1b2a3
Create Date: 2026-06-12
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "c4d8e1f9a7b2"
down_revision: Union[str, None] = "f8e9d0c1b2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("task", "reader", "view"),
    ("task", "operator", "view"),
    ("task", "admin", "view"),
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
        "WHERE entity_type = 'task' AND action = 'view'"
    )
