"""prepare-for-test: режим безопасности Astra (mode)

Revision ID: fbad9bc3b9dd
Revises: b3d7f6a1c852
Create Date: 2026-09-08 16:20:00.000000

Пайплайн `prepare-for-test` (§5.1 плана) между сменой ядра и финальным
ребутом ещё выставляет режим безопасности Astra (`astra-modeswitch`) —
добавлено к заданию задним числом, уже после того, как
`b3d7f6a1c852` завела сами таблицы. Меняем их точечно, а не переписываем
исходную ревизию:

  * новая колонка `mode` (`orel` / `smolensk`) — обязательна, third value
    (воронеж) не заводим;
  * новый шаг `mode_switch` в списке `failed_step`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fbad9bc3b9dd"
down_revision: Union[str, None] = "b3d7f6a1c852"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "server_prepare_for_test_requests"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "mode", sa.String(length=16), nullable=False, server_default="orel"
        ),
    )
    op.create_check_constraint(
        "ck_prepare_for_test_mode", _TABLE, "mode IN ('orel', 'smolensk')",
    )
    op.drop_constraint("ck_prepare_for_test_failed_step", _TABLE, type_="check")
    op.create_check_constraint(
        "ck_prepare_for_test_failed_step",
        _TABLE,
        "failed_step IS NULL OR failed_step IN "
        "('restore', 'prepare', 'user_provision', 'kernel_change', "
        "'mode_switch', 'reboot_verify')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_prepare_for_test_failed_step", _TABLE, type_="check")
    op.create_check_constraint(
        "ck_prepare_for_test_failed_step",
        _TABLE,
        "failed_step IS NULL OR failed_step IN "
        "('restore', 'prepare', 'user_provision', 'kernel_change', "
        "'reboot_verify')",
    )
    op.drop_constraint("ck_prepare_for_test_mode", _TABLE, type_="check")
    op.drop_column(_TABLE, "mode")
