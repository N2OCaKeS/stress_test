"""Схема ответа `GET /test-stands/{id}/orchestration-log` (P2-остаток, №4)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class QueueOrchestrationEventResponse(BaseModel):
    """Одно диагностическое событие диспетчера очереди стенда."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    stand_id: str
    queue_item_id: str | None = None
    kind: str
    detail: str | None = None
    created_at: datetime
