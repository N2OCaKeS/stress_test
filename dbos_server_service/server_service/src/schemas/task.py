"""Pydantic-схемы для cancel-endpoint'а worker-task'и.

Сама модель `Task` живёт в server_worker (`tasks` таблица в
dev_server_worker). server_service ходит туда через cross-DB engine из
`worker_client` — отдельной ORM-модели здесь не заводим, только request/
response shape'ы для endpoint'а.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class TaskCancelRequest(BaseModel):
    """Тело POST /tasks/{task_id}/cancel.

    `reason` — опциональный свободный текст для журнала. Пишется в
    `tasks.cancel_reason` (truncated до 512 символов на стороне БД) и
    форвардится в audit details. Без reason ставится NULL — для UI
    «отмена по кнопке» это нормальный кейс.
    """

    reason: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Опциональная причина отмены (пишется в tasks.cancel_reason и "
            "в audit details). До 512 символов."
        ),
    )


class TaskCancelResponse(BaseModel):
    """Ответ на успешный cancel: финальное состояние row."""

    task_id: str = Field(description="ID отменённой задачи.")
    status: str = Field(description="Финальный статус (всегда 'cancelled').")
    previous_status: str = Field(
        description="Статус до отмены ('queued' или 'running').",
    )
    cancelled_at: datetime = Field(
        description="UTC-метка решения отменить (момент UPDATE'а).",
    )
    cancelled_by: str | None = Field(
        default=None,
        description="user_id оператора, дёрнувшего cancel. None для bot-token'ов без sub.",
    )
    cancel_reason: str | None = Field(
        default=None,
        description="Причина отмены из тела запроса (или None).",
    )
