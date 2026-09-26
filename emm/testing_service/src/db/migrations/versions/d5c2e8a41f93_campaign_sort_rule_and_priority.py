"""Campaign sort rule and test priority

Revision ID: d5c2e8a41f93
Revises: e4d17a2c9b60
Create Date: 2026-09-24 00:00:00.000000

Решение D15 — порядок тестов на стенде при прогоне всей РЦ.

- `department_test_settings.campaign_sort_rule` — упорядоченный список ключей
  сортировки кампании (`[{"key": ..., "direction": "asc"|"desc"}, ...]`,
  ключи из белого списка `mode`/`kernel`/`test_case_name`/`test_code`/
  `priority`, см. `schemas/department_test_settings.py`). Сид — легаси-правило
  `emm/allta_app_full/allta_back.py:393`: `sorted(dates_list)`, где элемент —
  `[[версия, режим, ядро, стенд], имя тест-кейса, статус]` (разбор имени
  тест-рана — `allta_back.py:494`), то есть режим → ядро → имя тест-кейса,
  все по возрастанию строкой. Версия и стенд в пределах очереди одного
  стенда одной РЦ одинаковы и на порядок не влияют. Существующие строки
  получают то же значение через `server_default`.
- `test_definitions.priority` — целочисленный приоритет теста для ключа
  `priority` правила. 0 у всех — легаси приоритетов не знал, поэтому
  дефолтное правило этот ключ не использует.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d5c2e8a41f93"
down_revision: Union[str, None] = "e4d17a2c9b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# allta_back.py:393 — sorted([[версия, режим, ядро, стенд], имя тест-кейса, статус]).
_LEGACY_SORT_RULE = (
    '[{"key": "mode", "direction": "asc"}, '
    '{"key": "kernel", "direction": "asc"}, '
    '{"key": "test_case_name", "direction": "asc"}]'
)


def upgrade() -> None:
    op.add_column(
        "department_test_settings",
        sa.Column(
            "campaign_sort_rule", JSONB(), nullable=False,
            server_default=sa.text(f"'{_LEGACY_SORT_RULE}'::jsonb"),
        ),
    )
    op.add_column(
        "test_definitions",
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("test_definitions", "priority")
    op.drop_column("department_test_settings", "campaign_sort_rule")
