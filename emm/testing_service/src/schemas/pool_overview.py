"""Pydantic-схемы GET /pool-overview (§F плана 2026-09-11).

Один ответ несёт оба независимых блока: очередь/исходы (`remaining`/
`running`/`succeeded`/`failed`) и статусы стендов (`stands` + `stand_status_counts`).
`test_run` заполнен только при `context=run` — заголовок карточки (РЦ, ядро,
режим) без отдельного похода клиента за кампанией.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StandStatusLiteral = Literal["recovering", "unreachable", "testing", "testing_done", "ready", "no_data"]


class PoolOverviewTestRun(BaseModel):
    """Заголовок выбранной кампании — только то, что нужно карточке контекста."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    os_version_id: str
    kernel: str
    mode: str | None = Field(
        default=None,
        description="Легаси-поле кампании — новые кампании его не пишут (режим теперь у каждого теста отдельно).",
    )
    status: str
    final: bool
    created_at: datetime


class PoolOverviewStand(BaseModel):
    """Один стенд пула с посчитанным статусом."""

    stand_id: str = Field(description="test_stands.id")
    server_id: str = Field(description="Server/Vm.id в server_service")
    status: StandStatusLiteral = Field(
        description=(
            "Восстанавливается/Недоступен/Тест идёт/Готов/Нет данных. "
            "Приоритет и определения — §F плана 2026-09-11."
        ),
    )
    busy_state: str | None = Field(default=None, description="Сырой busy_state server_service, если данные есть.")
    busy_service_name: str | None = Field(default=None, description="Имя сервиса-держателя брони, если есть.")
    ping_reachable: bool | None = Field(default=None, description="Последний живой сигнал ping.")
    ping_checked_at: datetime | None = Field(default=None, description="Момент последнего ping-замера (UTC).")


class PoolOverviewResponse(BaseModel):
    """Ответ GET /pool-overview."""

    context: Literal["all", "run", "standalone"]
    test_run_id: str | None = None
    test_run: PoolOverviewTestRun | None = Field(
        default=None, description="Только при context=run — заголовок кампании.",
    )
    remaining: int = Field(description="queued+preparing+ready — осталось выполнить.")
    running: int = Field(description="Выполняются сейчас.")
    succeeded: int = Field(description="Логические тесты, у которых последняя попытка — succeeded.")
    failed: int = Field(description="Логические тесты, у которых последняя попытка — failed.")
    stands: list[PoolOverviewStand]
    stand_status_counts: dict[StandStatusLiteral, int] = Field(
        description="Число стендов в каждом статусе — свёртка `stands` для карточек.",
    )
    generated_at: datetime = Field(description="Момент сборки этого ответа (UTC) — для отметки времени обновления на UI.")
