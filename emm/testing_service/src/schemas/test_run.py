"""Pydantic-схемы для /test-runs (§2.4, §6.1 плана миграции).

`TestRunCreate` — вход кампании: РЦ+ядро+режим+явный список стендов пула,
`final` — официальный/финальный прогон релиза (просто сохраняется, влияние на
интеграцию со СТП — волна 8). `department_id` в теле нет — кампания
привязывается к отделу инициатора (`identity.department_id`), не может быть
подделана в запросе.

`TestRunCreateResponse` расширяет обычную карточку двумя списками —
`stands_without_tests` (стенд из пула без единого закреплённого теста, не
рушит остальную кампанию) и `enqueue_errors` (частичные провалы постановки в
очередь одного конкретного теста одного стенда — тоже не рушат остальную
кампанию, см. `services/test_run.py`).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TestRunCreate(BaseModel):
    """Тело POST /test-runs."""

    os_version_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Id карточки версии ОС в каталоге server_service (кладётся в launch_context.RC как есть).",
    )
    mode: str = Field(
        ..., min_length=1, max_length=16,
        description="Режим безопасности Astra (`orel`/`smolensk`) — тот же домен, что у launch_context.MODE.",
    )
    kernel: str = Field(
        ..., min_length=1, max_length=64,
        description="Версия ядра, кладётся в launch_context.KERNEL.",
    )
    test_run_stands: list[str] = Field(
        ..., min_length=1,
        description="Явно выбранный оператором пул стендов (test_stands.id) — вход запроса, не авто-вычисляется.",
    )
    final: bool = Field(
        default=False,
        description="Официальный/финальный прогон релиза — просто сохраняется, влияние на СТП появится в волне 8.",
    )


class TestRunPartialError(BaseModel):
    """Один частичный провал постановки в очередь одного теста одного стенда кампании."""

    stand_id: str
    test_id: str
    error_code: str
    message: str


class TestRunResponse(BaseModel):
    """Карточка кампании."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Test run ID (prefix run_).")
    os_version_id: str
    mode: str
    kernel: str
    department_id: str = Field(description="Отдел-инициатор кампании.")
    test_run_stands: list[str] = Field(description="Пул стендов, выбранный при создании.")
    status: str = Field(description="Агрегатный статус: queued/running/succeeded/failed/partially_failed.")
    final: bool
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None


class TestRunCreateResponse(TestRunResponse):
    """Ответ POST /test-runs — карточка + отчёт о том, что не удалось поставить в очередь."""

    stands_without_tests: list[str] = Field(
        default_factory=list,
        description="Стенды пула, у которых не нашлось ни одного закреплённого теста.",
    )
    enqueue_errors: list[TestRunPartialError] = Field(
        default_factory=list,
        description="Частичные провалы постановки в очередь отдельных тестов — остальная кампания не пострадала.",
    )


class TestRunQueueItemResponse(BaseModel):
    """Один дочерний queue_item в детальной карточке кампании (GET /test-runs/{id})."""

    queue_item_id: str
    stand_id: str
    test_id: str
    state: str
    is_retry: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class TestRunDetailResponse(TestRunResponse):
    """Ответ GET /test-runs/{id} — карточка + все дочерние queue_items."""

    queue_items: list[TestRunQueueItemResponse] = Field(default_factory=list)
