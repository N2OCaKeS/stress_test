"""Pydantic-схемы для cancel-endpoint'а worker-task'и.

Сама модель `Task` живёт в server_worker (`tasks` таблица в
dev_server_worker). server_service ходит туда через cross-DB engine из
`worker_client` — отдельной ORM-модели здесь не заводим, только request/
response shape'ы для endpoint'а.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskRead(BaseModel):
    """Карточка одной worker-task'и для list/detail чтения.

    Row живёт в `dev_server_worker.tasks` (см. `server_worker/src/models/
    task.py`); server_service читает её через cross-DB engine из
    `worker_client`. Имена полей здесь — стабильный API-контракт UI, поэтому
    кое-где отличаются от колонок таблицы:

    * `kind` ← колонка `task_kind`;
    * `server_id` ← `target_server_id`;
    * `account_id` ← `target_resource_id` (secondary target — server_account
      id у password/provision-задач);
    * `created_at` ← `enqueued_at` (момент постановки);
    * `started_at` / `finished_at` ← `started_at` / `completed_at`;
    * `retry_count` ← `attempt`.

    `department_id` в самой таблице нет — он резолвится server_service'ом из
    `server.department_id` по `server_id`. Для инфра-задач без `server_id`
    (scheduler/heartbeat/sweep/cleanup) остаётся `None`.

    `result` в листинге усекается до summary (`_summarize_result`), в detail
    отдаётся целиком.
    """

    id: str = Field(description="task_id (PK в dev_server_worker.tasks).")
    kind: str = Field(description="task_kind — taskiq-label (`power.on`, `inventory.sync`, ...).")
    status: str = Field(
        description="queued / running / succeeded / failed / cancelled.",
    )
    server_id: str | None = Field(
        default=None,
        description="target_server_id. None у инфра-задач без конкретного сервера.",
    )
    account_id: str | None = Field(
        default=None,
        description="target_resource_id — secondary target (server_account id и т.п.).",
    )
    server_hostname: str | None = Field(
        default=None,
        description=(
            "Человекочитаемый hostname сервера из `server_id`. None у инфра-"
            "задач без сервера и если сервер уже удалён."
        ),
    )
    account_login: str | None = Field(
        default=None,
        description=(
            "Логин server_account'а из `account_id`. None если задача без "
            "аккаунта или аккаунт уже удалён."
        ),
    )
    department_id: str | None = Field(
        default=None,
        description=(
            "Отдел задачи, резолвится из server.department_id по server_id. "
            "None для инфра-задач без сервера."
        ),
    )
    created_by: str | None = Field(
        default=None,
        description=(
            "user_id инициатора задачи (из dev_server_worker.tasks.created_by). "
            "None у инфра-задач (heartbeat/sweep/cleanup) и dispatch'ей без актора."
        ),
    )
    created_at: datetime = Field(description="enqueued_at — момент постановки задачи.")
    started_at: datetime | None = Field(
        default=None, description="Момент старта исполнения (None пока queued).",
    )
    finished_at: datetime | None = Field(
        default=None, description="Момент финала (completed_at), None пока не терминальна.",
    )
    retry_count: int = Field(description="attempt — номер текущей попытки (0 на первой).")
    last_error: str | None = Field(
        default=None, description="Текст последней ошибки (для detail / DLQ-фильтра failed).",
    )
    result: Any | None = Field(
        default=None,
        description=(
            "JSONB-результат. В листинге — усечённое summary, в detail — полностью."
        ),
    )


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
