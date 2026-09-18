"""Публичные операции запуска принимают только несекретный контекст ОС.

`mode` здесь намеренно нет — режим безопасности фиксирован на самом тесте
(`test_definitions.mode`), запускающий не выбирает его."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class QueueLaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128)
    test_id: str = Field(min_length=1, max_length=64)
    stand_id: str = Field(min_length=1, max_length=64)
    os_version_id: str = Field(min_length=1, max_length=64)
    kernel: str = Field(min_length=1, max_length=64)
    debug_mode: bool = False
    prepare_only: bool = Field(
        default=False,
        description=(
            "Только откатить/подготовить стенд (легаси testenv), не запускать "
            "тест. Вместо результата теста воркер оставляет на стенде "
            "command.txt с командой, которая была бы запущена."
        ),
    )


class QueueRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128)


class PublicQueueItem(BaseModel):
    log_status: str = "missing"
    test_code: str | None = None
    test_name: str | None = None
    is_current: bool = True
    id: str
    test_id: str
    stand_id: str
    test_run_id: str | None
    retry_of_id: str | None
    debug_mode: bool
    prepare_only: bool
    state: str
    rc: str | None
    kernel: str | None
    mode: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    # Запрошенное прерывание (`skip`/`pause`), пока воркер его не отработал.
    # Непустое значение видно только у `running`-элемента — фронт по нему
    # показывает «Останавливается…» и дизейблит кнопки управления очередью.
    interrupt_action: str | None = None
