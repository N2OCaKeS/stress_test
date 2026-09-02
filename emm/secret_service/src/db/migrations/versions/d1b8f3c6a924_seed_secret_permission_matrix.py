"""seed secret permission matrix (guest/admin)

Системные роли secret_service несут фиксированную тип-wide матрицу:

* ``guest`` → ``read`` (метаданные/листинг неличных секретов отдела, без
  значений);
* ``admin`` → все грантуемые действия сущности ``secret``
  (``read``/``reveal``/``write``/``delete``/``grant_acl``/``grant_dept``/
  ``manage_status``).

Строки system-wide (``department_id IS NULL``). Семантика этих ролей и так
зашита в `access_service` (guest короткозамыкает на view, admin — на полный
доступ), но матрица должна их показывать; grant/revoke по ним заблокированы
как SYSTEM_ROLE_IMMUTABLE. Идемпотентно — вставка через WHERE NOT EXISTS.

Revision ID: d1b8f3c6a924
Revises: c9a4e2f7b103
Create Date: 2026-07-01 10:05:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "d1b8f3c6a924"
down_revision: Union[str, None] = "c9a4e2f7b103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (role, action) → детерминированный id сида.
_SEED_GRANTS: list[tuple[str, str]] = [
    ("guest", "read"),
    ("admin", "read"),
    ("admin", "reveal"),
    ("admin", "write"),
    ("admin", "delete"),
    ("admin", "grant_acl"),
    ("admin", "grant_dept"),
    ("admin", "manage_status"),
]


def upgrade() -> None:
    for role, action in _SEED_GRANTS:
        seed_id = f"prm_seed_{role}_{action}"
        op.execute(
            f"""
            INSERT INTO entity_permissions (id, entity_type, role, action)
            SELECT '{seed_id}', 'secret', '{role}', '{action}'
            WHERE NOT EXISTS (
                SELECT 1 FROM entity_permissions
                WHERE entity_type = 'secret'
                  AND role = '{role}'
                  AND action = '{action}'
                  AND department_id IS NULL
            )
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM entity_permissions
         WHERE entity_type = 'secret'
           AND role IN ('guest', 'admin')
           AND department_id IS NULL
        """
    )
