"""seed (task, cancel) grant for admin

Адресует endpoint `POST /api/server/v1/tasks/{id}/cancel`. Сама пара
`(task, cancel)` добавлена в `constants.ENTITY_ACTIONS[task]` — этот
seed подкатывает дефолтный grant ровно как делают 831ba55543e9 и
e3f8c4b21a07 для своих action'ов.

* admin — получает: cancel доступен только держателям admin-роли по дефолту.
* operator/reader — НЕ получают: отмена чужой task'и — операция уровня
  admin'а. При необходимости grant можно выдать вручную через
  `(permission, permission_grant)`.
* worker_bot — НЕ получает: воркер сам ничего не отменяет, он только
  читает status через CAS на mark_running.

Revision ID: a8d2b7c1e394
Revises: f2a1b8c9d3e4
Create Date: 2026-05-30
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "a8d2b7c1e394"
down_revision: Union[str, None] = "f2a1b8c9d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("task", "admin", "cancel"),
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
        "WHERE entity_type = 'task' AND action = 'cancel'"
    )
