"""Pydantic-схемы логов прогонов (§2.6, §8 плана миграции).

`LogChunkRequest`/`LogSegmentRequest` — internal-контракт приёма от
`testing_worker` (см. `api/v1/endpoints/internal_log.py`, где он подробно
задокументирован буквально) — следующая волна (потоковое SSH-исполнение в
`testing_worker`) строит отправку на этих двух схемах без права менять их
форму в одностороннем порядке.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LogChunkRequest(BaseModel):
    """Тело POST /internal/queue/{queue_item_id}/log-chunk."""

    text: str = Field(description="Сырой инкрементальный вывод ещё не закрытой команды.")


class LogSegmentRequest(BaseModel):
    """Тело POST /internal/queue/{queue_item_id}/log-segment.

    Один уже завершённый шаг (checkpoint/command) с готовым результатом.
    `testing_service` форматирует блок легаси-формата сам (см.
    `services/test_log.py::format_block`) — воркер шлёт только структурные
    поля, никогда готовый текст.
    """

    kind: Literal["checkpoint", "command"]
    label: str = Field(min_length=1, max_length=256, description="Имя вехи/команды — попадает в `TASK [label: host]`.")
    status: Literal["OK", "CHANGED", "FATAL"]
    command_text_masked: str | None = Field(
        default=None, max_length=4096,
        description="Только для kind=command. Креды уже замаскированы вызывающим (§8.1) — сырых значений сюда не попадает.",
    )
    output: str = Field(default="", description="Захваченный вывод — попадает в `CONCLUSION:`.")
    host: str = Field(min_length=1, max_length=255, description="Стенд, на котором выполнялся шаг.")
    started_at: datetime = Field(description="Момент старта шага — отображается в блоке, конвертируется в MSK.")
    finished_at: datetime = Field(description="Момент завершения шага.")


class LogAppendResponse(BaseModel):
    """Ответ обоих internal-эндпоинтов приёма лога."""

    ok: bool = True
    log_id: str


class TestLogSegmentResponse(BaseModel):
    """Один сегмент в ответе `GET /queue-items/{id}/log/segments`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    log_id: str
    position: int
    kind: str
    label: str
    command_text_masked: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    byte_offset_start: int
    byte_offset_end: int | None
