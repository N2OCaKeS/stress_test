"""add retired_key_versions

Durable-пометка выведенных версий мастер-ключа. KeyStore при отсутствии
файла bootstrap'ится из env, а в прод-k8s файл живёт на emptyDir и пропадает
при рестарте пода — без этой таблицы выведенная версия воскресла бы из
`SECRET_ENCRYPTION_KEY__v<N>`. На старте сервиса reconcile сверяет таблицу с
keystore и вычищает воскресшие версии.

Schema:

* PK `version BIGINT` — номер выведенной версии.
* `retired_at` — когда версию вывели.

Revision ID: b8d1f0a3c692
Revises: a7c9e1f4b832
Create Date: 2026-06-29 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8d1f0a3c692"
down_revision: Union[str, None] = "a7c9e1f4b832"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retired_key_versions",
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "retired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("version"),
    )


def downgrade() -> None:
    op.drop_table("retired_key_versions")
