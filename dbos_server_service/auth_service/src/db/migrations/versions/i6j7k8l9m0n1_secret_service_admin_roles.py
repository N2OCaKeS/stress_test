"""secret_service admin roles + service_admin platform role

Revision ID: i6j7k8l9m0n1
Revises: h5i6j7k8l9m0
Create Date: 2026-06-08 12:00:00.000000

Две независимые правки, объединённые одной миграцией ради семантической
связности «секрет-сервис получает свой админский плоскостной слой»:

1) `users.platform_role` CHECK обновляется, чтобы пропустить новое
   значение `service_admin` (cross-dept админ secret_service:
   read_for_audit / admin_override_delete / transfer_ownership / recover).
   Прежний набор `account_admin / department_admin / loging_admin /
   loging_reader` остаётся валидным.

2) Каждой dept'е, у которой уже выдан access к `secret_service`,
   досевается системная роль `admin` в `service_role_definitions`
   (`is_system=True`). При штатной выдаче доступа `seed_system_admin`
   делает это сам, но миграция страхует ранее выданные access'ы.

Downgrade откатывает CHECK к прежнему набору; роли `admin` не сносит
(soft-delete сделал бы deactivate каскадно по grant_service_access
заново — оставляем idempotent state).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "i6j7k8l9m0n1"
down_revision: Union[str, None] = "h5i6j7k8l9m0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ALLOWED_NEW = (
    "account_admin",
    "department_admin",
    "loging_admin",
    "loging_reader",
    "service_admin",
)
_ALLOWED_OLD = (
    "account_admin",
    "department_admin",
    "loging_admin",
    "loging_reader",
)


def upgrade() -> None:
    # ── 1. CHECK constraint: добавить `service_admin` ─────────────────────
    op.drop_constraint("ck_users_platform_role", "users", type_="check")
    values = ", ".join(f"'{v}'" for v in _ALLOWED_NEW)
    op.create_check_constraint(
        "ck_users_platform_role",
        "users",
        f"platform_role IS NULL OR platform_role IN ({values})",
    )

    # ── 2. Досев `admin` для каждой dept'ы с access'ом к secret_service ───
    # id — короткий хеш чтобы UNIQUE (department_id, service_name, role_name)
    # не споткнулся, и чтобы записи отличались от выданных через REST.
    # created_by=NULL — миграция, не actor.
    op.execute(
        """
        INSERT INTO service_role_definitions
            (id, department_id, service_name, role_name, display_name,
             description, is_active, is_system, created_at, created_by)
        SELECT
            'srd_mig_' || substring(md5(dsa.department_id || ':secret_service:admin'), 1, 24),
            dsa.department_id,
            'secret_service',
            'admin',
            'Admin',
            'Full administrative access to the service',
            TRUE,
            TRUE,
            now(),
            NULL
        FROM department_service_access dsa
        WHERE dsa.service_name = 'secret_service'
          AND dsa.is_active = TRUE
        ON CONFLICT (department_id, service_name, role_name) DO UPDATE
            SET is_active = TRUE,
                is_system = TRUE
        """
    )


def downgrade() -> None:
    # CHECK constraint откатываем к прежнему набору. Если в таблице
    # users успели появиться записи с platform_role='service_admin' —
    # downgrade упадёт; такие строки нужно вычистить руками заранее.
    op.drop_constraint("ck_users_platform_role", "users", type_="check")
    values = ", ".join(f"'{v}'" for v in _ALLOWED_OLD)
    op.create_check_constraint(
        "ck_users_platform_role",
        "users",
        f"platform_role IS NULL OR platform_role IN ({values})",
    )

    # `admin` system role оставляем — она и так сидится автоматически при
    # grant_service_access; снос её ломает все секрет-сервисные dept'ы.
