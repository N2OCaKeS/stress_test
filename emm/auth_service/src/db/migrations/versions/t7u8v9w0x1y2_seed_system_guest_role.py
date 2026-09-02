"""seed system `guest` role for active department-service access

Revision ID: t7u8v9w0x1y2
Revises: s6t7u8v9w0x1
Create Date: 2026-06-29 00:00:00.000000

Раньше при выдаче отделу доступа к сервису в каталог сеялась только системная
роль `admin`. Теперь сидим ещё и `guest`. Эта миграция досевает недостающий
`guest` для всех АКТИВНЫХ пар (department, service): `admin` уже сидится
исторически и здесь не трогается.

Idempotent: если `guest` для пары уже есть (например, отдел успел завести
одноимённую кастомную роль), строка не дублируется — её только реактивируют и
помечают системной (ON CONFLICT по uq_dept_service_role_name).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "t7u8v9w0x1y2"
down_revision: Union[str, None] = "s6t7u8v9w0x1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Один INSERT ... SELECT на все активные пары. id генерим в формате
    # `srd_<32 hex>` через md5 — без зависимости от расширений pgcrypto.
    # ON CONFLICT promote'ит существующую (в т.ч. кастомную) `guest`-строку до
    # системной, не плодя дублей.
    op.execute(
        """
        INSERT INTO service_role_definitions
            (id, department_id, service_name, role_name, description,
             is_active, is_system, created_at)
        SELECT
            'srd_' || md5(random()::text || clock_timestamp()::text
                          || dsa.department_id || dsa.service_name),
            dsa.department_id,
            dsa.service_name,
            'guest',
            'Baseline guest access to the service',
            TRUE,
            TRUE,
            now()
        FROM department_service_access dsa
        WHERE dsa.is_active = TRUE
        ON CONFLICT ON CONSTRAINT uq_dept_service_role_name DO UPDATE
            SET is_active = TRUE,
                is_system = TRUE
        """
    )


def downgrade() -> None:
    # Деактивируем только досеянный системный `guest`. `admin` не трогаем —
    # он жил в каталоге и до этой миграции.
    op.execute(
        """
        UPDATE service_role_definitions
           SET is_active = FALSE
         WHERE role_name = 'guest'
           AND is_system = TRUE
        """
    )
