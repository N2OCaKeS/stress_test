"""drop reader/operator system role grants

Revision ID: a4d9f3e8c1b7
Revises: f2c8b1d6e4a9
Create Date: 2026-06-30 10:00:00.000000

Системными service-ролями server_service остаются только ``guest`` (базовый
доступ) и ``admin`` (полный). Весь промежуточный доступ теперь идёт через
кастомные роли — их грантят per-department строками ``entity_permissions``.
Роли ``reader`` и ``operator`` системными больше не считаются и не сеются.

Эта миграция вычищает из матрицы все грантовые строки этих двух ролей —
и system-wide (``department_id IS NULL`` из seed-миграций), и per-department,
если кто-то завёл их вручную под старую модель. После этого reader/operator
исчезают из матрицы доступа в UI.

Downgrade — no-op: восстановить прежний набор грантов невозможно (их
точный состав менялся десятком миграций, а кастомные one-off строки в коде
не зафиксированы). Откат модели делается не возвратом этих строк, а отдельным
ручным сидингом при необходимости.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a4d9f3e8c1b7"
down_revision: Union[str, None] = "f2c8b1d6e4a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM entity_permissions
         WHERE role IN ('reader', 'operator')
        """
    )


def downgrade() -> None:
    # Невосстановимо: прежний набор reader/operator-грантов не фиксирован
    # одним местом. Оставляем no-op намеренно.
    pass
