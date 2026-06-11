"""collapse UserGroup.display_name into name

Revision ID: n1o2p3q4r5s6
Revises: m0n1o2p3q4r5
Create Date: 2026-06-11 12:01:00.000000

Симметрично departments: `user_groups.display_name` переезжает в `name`,
колонка сносится. UNIQUE(`department_id`, `name`) сохраняется как есть.

Та же пара pre-check'ов перед UPDATE'ом: длина (String(256) → String(128)) и
дубли по `display_name` в рамках одного департамента — иначе на prod
схлопывание молча обрежет имена или упадёт на UNIQUE.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision: str = "n1o2p3q4r5s6"
down_revision: Union[str, None] = "m0n1o2p3q4r5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NAME_MAX_LEN = 128


def upgrade() -> None:
    bind = op.get_bind()

    too_long = bind.execute(
        text(
            "SELECT id, display_name FROM user_groups "
            "WHERE display_name IS NOT NULL AND LENGTH(display_name) > :max_len "
            "ORDER BY id LIMIT 5"
        ),
        {"max_len": _NAME_MAX_LEN},
    ).fetchall()
    if too_long:
        count_row = bind.execute(
            text(
                "SELECT COUNT(*) FROM user_groups "
                "WHERE display_name IS NOT NULL AND LENGTH(display_name) > :max_len"
            ),
            {"max_len": _NAME_MAX_LEN},
        ).scalar_one()
        sample = ", ".join(f"{r[0]}={r[1]!r}" for r in too_long)
        raise RuntimeError(
            f"NAME_TOO_LONG: {count_row} user_groups have display_name "
            f"longer than {_NAME_MAX_LEN} chars, refusing to collapse: {sample}"
        )

    # UNIQUE на user_groups — (department_id, name), поэтому дубли проверяем
    # тоже в разрезе department_id: один и тот же display_name в разных
    # отделах после схлопывания не конфликтует.
    dups = bind.execute(
        text(
            "SELECT department_id, display_name, COUNT(*) AS c FROM user_groups "
            "WHERE display_name IS NOT NULL "
            "GROUP BY department_id, display_name HAVING COUNT(*) > 1 "
            "ORDER BY c DESC LIMIT 5"
        )
    ).fetchall()
    if dups:
        sample = ", ".join(f"dept={r[0]}/{r[1]!r}×{r[2]}" for r in dups)
        raise RuntimeError(
            f"DUPLICATE_DISPLAY_NAME: collapse would violate user_groups "
            f"(department_id, name) uniqueness, examples: {sample}"
        )

    op.execute(
        "UPDATE user_groups "
        "SET name = display_name "
        "WHERE display_name IS NOT NULL AND TRIM(display_name) != ''"
    )
    op.drop_column("user_groups", "display_name")


def downgrade() -> None:
    op.add_column(
        "user_groups",
        sa.Column("display_name", sa.String(length=256), nullable=True),
    )
    op.execute("UPDATE user_groups SET display_name = name")
    op.alter_column("user_groups", "display_name", nullable=False)
