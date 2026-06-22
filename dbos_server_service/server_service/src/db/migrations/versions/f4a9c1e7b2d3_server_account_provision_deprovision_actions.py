"""promote server_account provision/deprovision to first-class matrix actions

Действия `provision` / `deprovision` стали полноценными грантами ролевой
матрицы `(server_account)` — раньше dispatch'и provision/deprovision гейтились
через `create`/`delete`, теперь по одноимённым действиям. Чтобы смена гейта не
отняла доступ у действующих ролей, бэкфиллим:

* `(server_account, <role>, provision)` для каждого существующего гранта
  `(server_account, <role>, create)`;
* `(server_account, <role>, deprovision)` для каждого `(server_account,
  <role>, delete)`.

Бэкфилл идёт по ВСЕМ строкам матрицы — и system-wide (`department_id IS NULL`),
и per-department кастомным ролям — с сохранением `department_id` исходного
гранта. Так admin/operator и любые кастомные роли, что несли create/delete,
получают provision/deprovision ровно в том же scope'е.

Insert идемпотентный (NOT EXISTS по той же тройке + department_id), чтобы
повторный прогон / пересечение с дефолт-seed'ом не плодил дублей и не падал
на partial-unique-индексах (`uq_entity_permissions_global` /
`uq_entity_permissions_per_dept`).

Downgrade снимает все `(server_account, provision)` и `(server_account,
deprovision)` строки.

Revision ID: f4a9c1e7b2d3
Revises: c2e8b6f1a473
Create Date: 2026-06-22
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "f4a9c1e7b2d3"
down_revision: Union[str, None] = "c2e8b6f1a473"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (исходное действие → производное действие) для бэкфилла по существующим
# грантам. provision наследует create, deprovision наследует delete.
_DERIVE: list[tuple[str, str]] = [
    ("create", "provision"),
    ("delete", "deprovision"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for source_action, new_action in _DERIVE:
        rows = conn.execute(
            sa.text(
                """
                SELECT role, department_id
                FROM entity_permissions
                WHERE entity_type = 'server_account'
                  AND action = :source_action
                """
            ).bindparams(source_action=source_action)
        ).fetchall()
        for role, department_id in rows:
            # NOT EXISTS guard — учитываем NULL-department корректно через
            # `IS NOT DISTINCT FROM` (NULL = NULL даёт NULL под обычным `=`).
            conn.execute(
                sa.text(
                    """
                    INSERT INTO entity_permissions
                        (id, entity_type, role, action, department_id)
                    SELECT :id, 'server_account', :role, :new_action,
                           CAST(:department_id AS VARCHAR)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM entity_permissions
                        WHERE entity_type = 'server_account'
                          AND role = :role
                          AND action = :new_action
                          AND department_id IS NOT DISTINCT FROM CAST(:department_id AS VARCHAR)
                    )
                    """
                ).bindparams(
                    id=f"prm_{uuid4().hex}",
                    role=role,
                    new_action=new_action,
                    department_id=department_id,
                )
            )


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_permissions "
        "WHERE entity_type = 'server_account' "
        "  AND action IN ('provision', 'deprovision')"
    )
