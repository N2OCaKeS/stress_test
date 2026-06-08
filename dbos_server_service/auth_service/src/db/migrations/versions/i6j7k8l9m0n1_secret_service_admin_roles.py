"""secret_service admin roles (no-op stub)

Revision ID: i6j7k8l9m0n1
Revises: h5i6j7k8l9m0
Create Date: 2026-06-08 12:00:00.000000

Файл сохранён в истории миграций, чтобы цепочка ревизий не порвалась на
средах, где он уже был применён. Тело свёрнуто в no-op после отказа от
платформенной роли `service_admin` и переноса админства secret_service в
per-(dept, service) service_roles. Системная роль `admin` сидится в
`service_role_definitions` непосредственно при выдаче departmental
service-access'а (`seed_system_admin`), поэтому ручной досев из этой
миграции тоже не нужен.

CHECK `ck_users_platform_role` управляется первичной миграцией, добавляющей
колонку `platform_role`. После удаления значения `service_admin` из enum'а
никаких rows с этим значением быть не должно — если на бою остались,
очистить руками SQL'ом перед накатом следующей миграции.
"""
from typing import Sequence, Union


revision: str = "i6j7k8l9m0n1"
down_revision: Union[str, None] = "h5i6j7k8l9m0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
