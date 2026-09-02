"""collapse Department.display_name into name

Revision ID: m0n1o2p3q4r5
Revises: l9m0n1o2p3q4
Create Date: 2026-06-11 12:00:00.000000

Сливаем `departments.display_name` в `departments.name`: identifier-как-slug
больше не нужен — он живёт через `id`, а пользователь видит и редактирует
только человеческое имя. Перед DROP'ом переносим непустые `display_name` в
`name`, чтобы UI не потерял текущие подписи.

На prod есть два сценария, при которых наивный `UPDATE name = display_name`
ломает данные:

* `display_name` длиннее 128 символов (String(256)) — silent truncate в
  String(128).
* UNIQUE-нарушение: разные `name`, одинаковый `display_name` → constraint
  violation при UPDATE.

Поэтому перед UPDATE прогоняем два pre-check'а и падаем с понятным
сообщением, если данные нельзя сжать без потерь.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision: str = "m0n1o2p3q4r5"
down_revision: Union[str, None] = "l9m0n1o2p3q4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NAME_MAX_LEN = 128


def upgrade() -> None:
    bind = op.get_bind()

    too_long = bind.execute(
        text(
            "SELECT id, display_name FROM departments "
            "WHERE display_name IS NOT NULL AND LENGTH(display_name) > :max_len "
            "ORDER BY id LIMIT 5"
        ),
        {"max_len": _NAME_MAX_LEN},
    ).fetchall()
    if too_long:
        count_row = bind.execute(
            text(
                "SELECT COUNT(*) FROM departments "
                "WHERE display_name IS NOT NULL AND LENGTH(display_name) > :max_len"
            ),
            {"max_len": _NAME_MAX_LEN},
        ).scalar_one()
        sample = ", ".join(f"{r[0]}={r[1]!r}" for r in too_long)
        raise RuntimeError(
            f"NAME_TOO_LONG: {count_row} departments have display_name "
            f"longer than {_NAME_MAX_LEN} chars, refusing to collapse: {sample}"
        )

    dups = bind.execute(
        text(
            "SELECT display_name, COUNT(*) AS c FROM departments "
            "WHERE display_name IS NOT NULL "
            "GROUP BY display_name HAVING COUNT(*) > 1 "
            "ORDER BY c DESC LIMIT 5"
        )
    ).fetchall()
    if dups:
        sample = ", ".join(f"{r[0]!r}×{r[1]}" for r in dups)
        raise RuntimeError(
            f"DUPLICATE_DISPLAY_NAME: collapse would violate departments.name "
            f"uniqueness, examples: {sample}"
        )

    op.execute(
        "UPDATE departments "
        "SET name = display_name "
        "WHERE display_name IS NOT NULL AND TRIM(display_name) != ''"
    )
    op.drop_column("departments", "display_name")


def downgrade() -> None:
    op.add_column(
        "departments",
        sa.Column("display_name", sa.String(length=256), nullable=True),
    )
    op.execute("UPDATE departments SET display_name = name")
    op.alter_column("departments", "display_name", nullable=False)
