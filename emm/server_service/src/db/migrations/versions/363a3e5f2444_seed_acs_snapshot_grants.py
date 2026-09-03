"""seed (server, acs_snapshot_*) grants for admin

Адресует новую фичу «снимки серверов через ACS» (Clonezilla-обёртка, полная
перезапись диска). Три action'а добавлены в `constants.ENTITY_ACTIONS[server]`.

* admin — получает все три: list / create / restore.
* reader / guest — НЕ получают: только просмотр карточек сервера.
* operator системным (department_id IS NULL) грантом не сеется — по решению
  владельца системные роли теперь только guest/admin, operator — кастомная
  per-department роль (см. Owner decisions 2026-06-29 в памяти); кто её
  заведёт себе в отделе, тому право выдаётся точечным грантом, а не сидом.

Revision ID: 363a3e5f2444
Revises: 3c6094335c43
Create Date: 2026-09-03
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "363a3e5f2444"
down_revision: Union[str, None] = "3c6094335c43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("server", "admin", "acs_snapshot_list"),
    ("server", "admin", "acs_snapshot_create"),
    ("server", "admin", "acs_snapshot_restore"),
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
        "WHERE entity_type = 'server' "
        "  AND action IN ('acs_snapshot_list', 'acs_snapshot_create', 'acs_snapshot_restore')"
    )
