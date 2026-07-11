"""boxes: download_status + download_last_error

Триггер скачивания бокса на hub (`box.download`) ведёт статус импорта на строке
бокса: `download_status` (downloading/ready/error) выставляется при dispatch'е и
обновляется callback'ом воркера; `download_last_error` несёт текст последней
ошибки. Оба nullable — у бокса без запусков скачивания их нет.

Downgrade: дроп обеих колонок.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2e6b8f4a1c7"
down_revision: Union[str, None] = "b1d7f3a9c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "boxes",
        sa.Column("download_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "boxes",
        sa.Column("download_last_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("boxes", "download_last_error")
    op.drop_column("boxes", "download_status")
