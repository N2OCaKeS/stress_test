"""Диагностический след оркестрации очереди стенда (P2-остаток, пункт №4).

Отдельная сущность от `test_log`/`test_log_segment` — та пара хранит текст и
навигацию по логу самого прогона теста, здесь — служебные события диспетчера
очереди (`services/queue.py`, `repositories/queue_item.py`), которые не
привязаны ни к какому конкретному прогону, а объясняют, почему поллинг
очереди не привёл к запуску теста. До появления этой таблицы такой ответ на
вопрос «почему тест не стартовал» не фиксировался нигде — были только
`logger.warning` про уже случившиеся best-effort сбои и аудит-события про уже
случившиеся переходы состояния, но не про попытки, которые ни к чему не
привели.

Не заводится на каждый пустой `claim()` — только когда есть что объяснить
оператору. `kind` — закрытый список, см. `QueueOrchestrationEventKind` в
`core/constants.py`.

`stand_id` — жёсткий FK с `CASCADE`, не снэпшот-метка, как у `test_logs`:
это оперативная диагностика с коротким ретеншном, ей незачем переживать
удаление стенда. `queue_item_id` — `SET NULL`, тем же приёмом, что у
`test_logs.queue_item_id`: конкретный item может быть удалён (`delete_item`),
а запись о том, что с ним происходило, пока хочется оставить.

Ретеншн — best-effort, без отдельного cron: каждая запись через
`services/queue_orchestration_log.py::record()` подчищает более старые
строки этого стенда сверх `_MAX_EVENTS_PER_STAND`. Это журнал для «что
происходило последние несколько часов», а не история для аудита.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class QueueOrchestrationEvent(Base):
    """Одно содержательное событие диспетчера очереди стенда."""

    __tablename__ = "queue_orchestration_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stand_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("test_stands.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    queue_item_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("queue_items.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    detail: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )
