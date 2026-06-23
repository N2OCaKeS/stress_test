"""seed (server, manage_packages) grant for admin and operator

Адресует `POST /api/server/v1/servers/packages/bulk-action` (массовые
install/remove/update пакетов через worker). Пара `(server, manage_packages)`
добавлена в `constants.ENTITY_ACTIONS[server]`; этот seed раскатывает дефолтные
гранты как d6c1f8a3b9e4 (console) / e3f8c4b21a07 (view_drift).

* admin / operator — получают: обслуживание состава пакетов на подготовленном
  сервере это оперативное действие, держателю operator-роли оно нужно для
  рутины (доустановить утилиту, выкатить обновление).
* reader / guest — НЕ получают: только чтение карточек и live-списка пакетов,
  без права менять состав на боксе.

Revision ID: d4f1a9c2e7b8
Revises: b9e1c7a3d206
Create Date: 2026-06-23
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d4f1a9c2e7b8"
down_revision: Union[str, None] = "b9e1c7a3d206"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("server", "admin", "manage_packages"),
    ("server", "operator", "manage_packages"),
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
        "WHERE entity_type = 'server' AND action = 'manage_packages'"
    )
