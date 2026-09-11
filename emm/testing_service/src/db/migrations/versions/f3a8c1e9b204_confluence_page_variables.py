"""add PARENT_PAGE and CONFLUENCE_NEW_PAGE global variables

Revision ID: f3a8c1e9b204
Revises: a7f4c9e2b816
Create Date: 2026-09-11 12:00:00.000000

Легаси (`allta_app/backup_image.py:289,296-297`) собирает два Confluence-флага:

  parent_page = args.PARP  (`-pp`, обязательный CLI-флаг)
  confluence_parent_page = f'--confluence-parent-page "{parent_page}"'
  confluence_new_page = f'--confluence-new-page "{TEST}_{RELEASE}_{MODE}_{KERNEL}_{STAND}"'

`PARENT_PAGE` — обычная `launch_context`-переменная, как `RC`/`KERNEL`/`MODE`:
значение приходит от вызывающего при постановке в очередь. `CONFLUENCE_NEW_PAGE`
тоже `source=launch_context`, но по факту её никто не передаёт — значение
вычисляется самим `queue.py::claim_next` (конкатенация `full_name`теста,
`RC`/`MODE`/`KERNEL` и `id` стенда) и кладётся в launch_context тем же кодом
непосредственно перед резолвом слотов, тем же путём, что и остальные
launch_context-переменные — отдельного source/резолвера под "вычисляемое
значение" заводить не стали, это осталось бы недостаточно общей абстракцией
ради одной переменной.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a8c1e9b204"
down_revision: Union[str, None] = "a7f4c9e2b816"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_VARIABLES: list[tuple[str, str, str, str, str | None, bool, str]] = [
    (
        "PARENT_PAGE", "Родительская страница Confluence", "launch_context", "string",
        None, False,
        "Легаси-флаг -pp/--confluence-parent-page. Задаётся вызывающим при постановке в очередь.",
    ),
    (
        "CONFLUENCE_NEW_PAGE", "Заголовок новой страницы Confluence", "launch_context", "string",
        None, False,
        "Легаси-флаг --confluence-new-page. Вычисляется автоматически "
        "queue.py::claim_next (full_name теста + RC + MODE + KERNEL + id "
        "стенда) — вызывающий это значение не задаёт, любое переданное "
        "им значение перезаписывается на claim.",
    ),
]


def upgrade() -> None:
    variables = sa.table(
        "global_variables",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("source", sa.String),
        sa.column("value_type", sa.String),
        sa.column("choices_source", sa.String),
        sa.column("is_sensitive", sa.Boolean),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        variables,
        [
            {
                "id": f"gvar_{code.lower()}",
                "code": code,
                "label": label,
                "source": source,
                "value_type": value_type,
                "choices_source": choices_source,
                "is_sensitive": is_sensitive,
                "description": description,
            }
            for code, label, source, value_type, choices_source, is_sensitive, description
            in _NEW_VARIABLES
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM global_variables WHERE code IN ('PARENT_PAGE', 'CONFLUENCE_NEW_PAGE')")
    )
