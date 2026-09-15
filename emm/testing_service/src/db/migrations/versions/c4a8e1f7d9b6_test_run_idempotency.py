"""test run idempotency

Revision ID: c4a8e1f7d9b6
Revises: b2f6a913c7d4
Create Date: 2026-09-15 00:00:00.000000

`test_runs.client_request_id`/`request_fingerprint` — та же идемпотентность,
что и у `queue_items` (§public_queue.py): повтор с тем же request_id и телом
возвращает уже созданную кампанию, повтор с тем же request_id и другим телом
— 409.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a8e1f7d9b6"
down_revision: Union[str, None] = "b2f6a913c7d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_runs", sa.Column("client_request_id", sa.String(length=128), nullable=True))
    op.add_column("test_runs", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_test_runs_client_request", "test_runs", ["created_by", "client_request_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_test_runs_client_request", "test_runs", type_="unique")
    op.drop_column("test_runs", "request_fingerprint")
    op.drop_column("test_runs", "client_request_id")
