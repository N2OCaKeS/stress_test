"""seed baseline matrix rows for the guest system role

Revision ID: f4a9d2c61b38
Revises: e5b1c7a34f92
Create Date: 2026-09-18 10:05:00.000000

Системная роль `guest` до сих пор не несла в `entity_permissions` ни одной
строки, хотя код о ней знает: `constants.SYSTEM_SERVICE_ROLES` перечисляет
её наравне с `admin`, а `permission_service._reject_system_role` пишет
«guest = view открытых каталогов» и запрещает править её матрицу через API.
Матрица при этом молчала — роль существовала только в коде.

Досеваем system-wide (`department_id IS NULL`) `view` на четыре
платформенных каталога, чтение которых и так открыто любому
аутентифицированному актору: `global_variable`, `test_definition`,
`test_stand`, `stp_test_case`. Тот же приём, что у
`server_service` (`f2c8b1d6e4a9`, `(server, guest, view)`) и
`secret_service` (`d1b8f3c6a924`, `(secret, guest, read)`).

Фактический доступ этим не меняется: `view` в testing_service нигде не
гейтится матрицей (см. `permissions.require_action` — VIEW проверяется
только для `permission` и `department_activity_report`), это
документирование существующей роли и её предполагаемого объёма.
Чувствительного guest'у не достаётся намеренно: `view_test_credentials`
(секрет учётки исполнения), `permission.view` (вся матрица прав),
`department_activity_report.view` (HR-данные отдела) в сид не входят.

Идемпотентность: INSERT ... WHERE NOT EXISTS, чтобы повторный прогон и
ручной hand-seed не уперлись в partial-unique `uq_entity_permissions_global`.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f4a9d2c61b38"
down_revision: Union[str, None] = "e5b1c7a34f92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (id, entity_type, action). id детерминированный — повторный прогон
# миграции на частично засеянной БД не плодит дублей.
_GUEST_BASELINE: list[tuple[str, str, str]] = [
    ("prm_seed_guest_global_variable_view", "global_variable", "view"),
    ("prm_seed_guest_test_definition_view", "test_definition", "view"),
    ("prm_seed_guest_test_stand_view", "test_stand", "view"),
    ("prm_seed_guest_stp_test_case_view", "stp_test_case", "view"),
]


def upgrade() -> None:
    for row_id, entity_type, action in _GUEST_BASELINE:
        op.execute(
            f"""
            INSERT INTO entity_permissions (id, entity_type, role, action)
            SELECT '{row_id}', '{entity_type}', 'guest', '{action}'
            WHERE NOT EXISTS (
                SELECT 1 FROM entity_permissions
                WHERE entity_type = '{entity_type}'
                  AND role = 'guest'
                  AND action = '{action}'
                  AND department_id IS NULL
            )
            """
        )


def downgrade() -> None:
    for _row_id, entity_type, action in _GUEST_BASELINE:
        op.execute(
            f"""
            DELETE FROM entity_permissions
             WHERE entity_type = '{entity_type}'
               AND role = 'guest'
               AND action = '{action}'
               AND department_id IS NULL
            """
        )
