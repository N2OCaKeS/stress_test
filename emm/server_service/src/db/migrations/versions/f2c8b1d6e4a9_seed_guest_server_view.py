"""seed guest server.view grant

Revision ID: f2c8b1d6e4a9
Revises: e1d4a7b2c9f3
Create Date: 2026-06-29 12:05:00.000000

Системная роль ``guest`` в server_service до сих пор не несла ни одного
action (seed 831ba55543e9 завёл её как placeholder без грантов). По модели
guest — базовый доступ к метаданным: имя и характеристики серверов отдела, но
БЕЗ чувствительного (ipmi credentials, пароли учёток, управляющие креды).

Эта миграция досевает единственный system-wide грант ``(server, guest, view)``.
Никаких чувствительных action'ов guest'у не выдаётся — view карточки сервера не
раскрывает ни паролей, ни ipmi-кред (для них нужны отдельные
``view_password`` / ``view_credentials`` / ``view_management_credentials``,
которых у guest нет).

Идемпотентность: вставка через WHERE NOT EXISTS, чтобы повторный прогон или
ручной hand-seed не упёрся в partial-unique ``uq_entity_permissions_global``.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f2c8b1d6e4a9"
down_revision: Union[str, None] = "e1d4a7b2c9f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO entity_permissions (id, entity_type, role, action)
        SELECT 'prm_seed_guest_server_view', 'server', 'guest', 'view'
        WHERE NOT EXISTS (
            SELECT 1 FROM entity_permissions
            WHERE entity_type = 'server'
              AND role = 'guest'
              AND action = 'view'
              AND department_id IS NULL
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM entity_permissions
         WHERE entity_type = 'server'
           AND role = 'guest'
           AND action = 'view'
           AND department_id IS NULL
        """
    )
