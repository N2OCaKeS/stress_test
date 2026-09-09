"""Сегмент лога — чекпоинт/команда внутри `test_log_blobs` (§2.6, §8.2 плана миграции).

Один сегмент = один блок легаси-формата (`services/test_log.py::format_block`)
внутри уже накопленного текста лога; `byte_offset_start`/`byte_offset_end`
адресуют этот блок для клика в UI без парсинга всего текста. Смещения —
индексы Python-строки (code points), не байты UTF-8: содержимое читается и
переписывается целиком в память на каждом append'е (ожидаемый объём — тексты
прогонов тестов, не гигабайты), поэтому внутренняя единица офсета не обязана
буквально быть байтом — важно только, что срез `content[start:end]`
согласован между записью и чтением, а это гарантирует единственная точка
записи (`services/test_log.py`).

`finished_at`/`byte_offset_end` формально nullable — задел под будущий
"открытый" сегмент (§8.6, live-стрим), которого этот контракт пока не
производит: `POST .../log-segment` всегда присылает уже завершённый шаг.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestLogSegment(Base):
    """Одна веха/команда внутри лога прогона."""

    __tablename__ = "test_log_segments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    log_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_logs.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    command_text_masked: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    byte_offset_start: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_offset_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
