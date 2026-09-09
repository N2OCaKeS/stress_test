"""Сырой текст лога, 1:1 к `test_logs` (§2.6 плана миграции).

Отдельная таблица, а не TEXT-колонка на `test_logs` — обычный SELECT
списка/карточки лога не должен таскать с собой потенциально длинный текст.
Растёт целиком через `content = content + <кусок>` на каждый чанк/сегмент
(см. `services/test_log.py`) — этого достаточно для ожидаемого объёма
(тексты прогонов тестов, не гигабайты).

Живёт в Postgres, а не на диске пода: `testing_service` разворачивается 2
репликами за одним Service без персистентного тома, только `test_logs`'ая БД
общая на все реплики.
"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestLogBlob(Base):
    """Содержимое одного лога — 1:1 к `TestLog` по PK=FK."""

    __tablename__ = "test_log_blobs"

    log_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_logs.id", ondelete="CASCADE"), primary_key=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
