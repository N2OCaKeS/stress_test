"""Pydantic-схемы для /test-runs (§2.4, §6.1 плана миграции).

`TestRunCreate` — вход кампании: РЦ+ядро, `final` — официальный/финальный
прогон релиза (влияет на обязательность СТП, `services/test_run.py`).
Режима здесь нет — он фиксирован на каждом тесте (`test_definitions.mode`),
кампания может законно смешивать orel- и smolensk-тесты, каждый готовится
под своим режимом (см. `TestRunEntryResponse.mode`). `department_id` в теле
нет — кампания привязывается к отделу инициатора (`identity.department_id`),
не может быть подделана в запросе.

`test_run_stands` теперь опционален. Заданный явно — прежнее поведение без
изменений: ровно эти стенды пула, тесты берутся по `pinned_stand_id` (группа
стенда, E3). Пустой/не заданный — состав кампании выводится из активного
состава СТП отдела для этого РЦ (полный прогон по РЦ): какие тесты, значит
какие стенды и ядра — решает СТП, оператор их не выбирает.

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
    kernel: str | None = Field(
        None, min_length=1, max_length=64,
        description="Версия ядра, кладётся в launch_context.KERNEL.",
    )
    test_run_stands: list[str] | None = Field(
        default=None,
        description=(
            "Явно выбранный оператором пул стендов (test_stands.id). Пусто/не "
            "задано — состав выводится из активного состава СТП этого РЦ для "
            "отдела вызывающего, стенды не выбираются оператором."
        ),
    )
    final: bool = Field(
        default=False,
        description="Официальный/финальный прогон релиза — просто сохраняется, влияние на СТП появится в волне 8.",
    )
    request_id: str | None = Field(
        None, min_length=8, max_length=128,
        description="Ключ идемпотентности: повтор с тем же значением и тем же телом вернёт уже созданную кампанию, с другим телом — 409.",
    )


class TestRunPreviewEntry(BaseModel):
    """Один тест кампании до постановки в очередь — что с ним произойдёт и почему."""

    stand_id: str
    test_id: str
    test_code: str
    test_name: str
    kernel: str
    mode: str = Field(description="Режим безопасности этого теста (test_definitions.mode).")
    action: str = Field(
        description="launch — будет поставлен в очередь; skip_debug_required/skip_stand_inactive/skip_not_in_stp — будет пропущен.",
    )
    reason: str | None = None


class TestRunPreviewResponse(BaseModel):
    """Ответ POST /test-runs/preview — состав кампании без побочных эффектов."""

    stands_without_tests: list[str] = Field(default_factory=list)
    entries: list[TestRunPreviewEntry] = Field(default_factory=list)


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
    mode: str | None = Field(
        default=None,
        description="Легаси, только у кампаний до этого изменения — новые кампании этого не пишут, режим смотрите в entries[].mode.",
    )
    kernel: str
    kernels: list[str] = Field(default_factory=list)
    department_id: str = Field(description="Отдел-инициатор кампании.")
    test_run_stands: list[str] = Field(description="Пул стендов кампании — явно выбранный либо выведенный из состава СТП, см. composition_source.")
    status: str = Field(description="Агрегатный статус: queued/running/succeeded/failed/partially_failed.")
    final: bool
    composition_source: str = Field(
        default="legacy_queue",
        description="pinned_catalog — явный test_run_stands; stp_composition — состав выведен из активной СТП; legacy_queue — до этого разделения.",
    )
    stp_composition_id: str | None = Field(
        default=None,
        description="Снэпшот stp_compositions.id, из которого выведен состав (только composition_source=stp_composition).",
    )
    stp_revision: int | None = Field(
        default=None,
        description="Ревизия состава СТП на момент создания кампании — последующее переключение состава не переписывает уже начатую кампанию.",
    )
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

    log_status: str = "missing"
    kernel: str | None = None
    queue_item_id: str
    stand_id: str
    test_id: str
    state: str
    is_retry: bool
    retry_of_id: str | None = None
    test_run_entry_id: str | None = None
    is_current: bool = True
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class TestRunEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    test_run_id: str
    stand_id: str
    test_id: str
    test_code: str
    test_name: str
    kernel: str | None = None
    mode: str = Field(description="Режим безопасности теста на момент постановки в очередь.")
    enqueue_error_code: str | None = None
    enqueue_error: str | None = None


class TestRunDetailResponse(TestRunResponse):
    """Ответ GET /test-runs/{id} — карточка + все дочерние queue_items."""

    queue_items: list[TestRunQueueItemResponse] = Field(default_factory=list)
    entries: list[TestRunEntryResponse] = Field(default_factory=list)
    progress: dict[str, int] = Field(default_factory=dict)
