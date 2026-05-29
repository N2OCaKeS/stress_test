"""seed (server, view_drift) grant for admin / operator

Адресует endpoint `GET /servers/{id}/drift` (агрегация
`server_account.drift_detected` из loging). Action добавлен в
`constants.ENTITY_ACTIONS[server]` — этот seed подкатывает дефолтный
grant ровно как делает 831ba55543e9 для остальных server-actions.

* admin — полный доступ (как и ко всем остальным action'ам).
* operator — получает: drift-summary полезна для оперативного присмотра
  за серверами, без раскрытия creds.
* reader — НЕ получает: инвариант «reader только view» сохраняется
  (см. `test_permission_matrix_roles.py::TestReaderOnlyViewGrants`).
  При необходимости grant можно дать вручную через `(permission,
  permission_grant)`.
* worker_bot — не получает: он эмитит drift-события, а не читает их.

Revision ID: e3f8c4b21a07
Revises: d1f4a8c7b3e9
Create Date: 2026-05-29
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "e3f8c4b21a07"
down_revision: Union[str, None] = "d1f4a8c7b3e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTS: list[tuple[str, str, str]] = [
    ("server", "admin", "view_drift"),
    ("server", "operator", "view_drift"),
]


def upgrade() -> None:
    # Идём через bulk_insert с явным id (prm_<uuid4>) — единый стиль с
    # `831ba55543e9_seed_default_entity_permissions`. Pre-check на
    # существование оставляем SQL'ным через NOT EXISTS, иначе при
    # re-apply словим unique-violation на partial global index
    # (`uq_entity_permissions_global` WHERE department_id IS NULL).
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
        "WHERE entity_type = 'server' AND action = 'view_drift'"
    )
