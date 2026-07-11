"""boxes table (VM image templates catalog) + box entity permissions

Таблица `boxes` — пер-департамент каталог боксов-заготовок для создания ВМ:
формат артефакта, источник скачивания, предустановленный в образе пользователь
(логин + зашифрованный пароль) и список ОС/снимков на диске изначально.

Плюс seed прав зоны `box` (admin — все действия, guest — view). Матрица
дублируется тут строкой, чтобы миграция была самодостаточной и пережила правки
constants.py.

Downgrade: дроп seed'а зоны box, дроп таблицы boxes.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "b1d7f3a9c2e4"
down_revision: Union[str, None] = "c4d8e1f9a2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Матрица прав зоны box (зеркало constants.ENTITY_ACTIONS[BOX]).
_BOX_ACTIONS: list[str] = ["view", "create", "update", "delete", "view_password"]


def upgrade() -> None:
    op.create_table(
        "boxes",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("download_url", sa.String(length=1024), nullable=True),
        sa.Column("base_user_login", sa.String(length=128), nullable=True),
        sa.Column("base_user_password_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "os_versions", ARRAY(sa.String()),
            nullable=False, server_default="{}",
        ),
        sa.Column(
            "initial_snapshots", ARRAY(sa.String()),
            nullable=False, server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_boxes_department_id", "boxes", ["department_id"])
    op.create_index(
        "uq_boxes_dept_name", "boxes", ["department_id", "name"], unique=True,
    )

    # ── seed прав зоны box: admin — всё, guest — view (system-wide) ────────────
    table = sa.table(
        "entity_permissions",
        sa.column("id", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("role", sa.String),
        sa.column("action", sa.String),
    )
    rows = [
        {"id": f"prm_{uuid4().hex}", "entity_type": "box", "role": "admin", "action": action}
        for action in _BOX_ACTIONS
    ]
    rows.append(
        {"id": f"prm_{uuid4().hex}", "entity_type": "box", "role": "guest", "action": "view"}
    )
    op.bulk_insert(table, rows)


def downgrade() -> None:
    op.execute("DELETE FROM entity_permissions WHERE entity_type = 'box'")
    op.drop_index("uq_boxes_dept_name", table_name="boxes")
    op.drop_index("ix_boxes_department_id", table_name="boxes")
    op.drop_table("boxes")
