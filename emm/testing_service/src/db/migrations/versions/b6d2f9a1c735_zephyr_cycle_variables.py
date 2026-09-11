"""add FOLDER_TREE_ID/TEST_CYCLE_NAME/TEST_CASE_NAME global variables

Revision ID: b6d2f9a1c735
Revises: f3a8c1e9b204
Create Date: 2026-09-11 13:00:00.000000

Легаси (`allta_app/backup_image.py:105-109,87-97,305-307`) принимает три
Zephyr-идентификатора обязательными CLI-флагами верхнего уровня и
подставляет их в каждую ветку `dates` под другими именами флагов:

  -cti   (dest=CTI,    help='cycle tree index')  -> fti  = f'-fti {args.CTI}'
  -tcyc  (dest=TCYCLE, help='test cycle name')    -> tcyc = f'-tcyc {args.TCYCLE}'
  -tcas  (dest=TCASE,  help='test case name')     -> tcas = f'-tcas "{args.TCASE}"'

Как и `RC`/`KERNEL`/`MODE`, это параметры конкретного запуска (Zephyr test
cycle/test case/folder-tree id), не секреты и не константы — значения
приходят от вызывающего при постановке в очередь (`launch_context`), той же
дорогой, что и остальные launch_context-переменные. В отличие от `-ba`
(Jira-токен) и `--username`/`--token`/`--confluence-space` — это не
Jira-креды и остаются плейсхолдером `"none"` до реализации §3.5.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6d2f9a1c735"
down_revision: Union[str, None] = "f3a8c1e9b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_VARIABLES: list[tuple[str, str, str, str, str | None, bool, str]] = [
    (
        "FOLDER_TREE_ID", "Zephyr folder-tree id", "launch_context", "string",
        None, False,
        "Легаси-флаг -cti/-fti (cycle tree index). Задаётся вызывающим при постановке в очередь.",
    ),
    (
        "TEST_CYCLE_NAME", "Название test cycle в Zephyr", "launch_context", "string",
        None, False,
        "Легаси-флаг -tcyc (test cycle name). Задаётся вызывающим при постановке в очередь.",
    ),
    (
        "TEST_CASE_NAME", "Название test case в Zephyr", "launch_context", "string",
        None, False,
        "Легаси-флаг -tcas (test case name). Задаётся вызывающим при постановке в очередь.",
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
        sa.text(
            "DELETE FROM global_variables WHERE code IN "
            "('FOLDER_TREE_ID', 'TEST_CYCLE_NAME', 'TEST_CASE_NAME')"
        )
    )
