"""Pydantic-схемы для /test-runs (§2.4, §6.1, §E1-E5 плана миграции).

`TestRunCreate` — вход кампании: РЦ+ядро, `final` — официальный/финальный
прогон релиза, чисто информационная метка (сохраняется, ни на что не влияет).
Допуск по СТП решает отдельный флаг `debug`, а не `final` — см. `debug`/`full`
ниже и `services/test_run.py`. Режима здесь нет — он фиксирован на каждом
тесте (`test_definitions.mode`), кампания может законно смешивать orel- и
smolensk-тесты, каждый готовится под своим режимом (см.
`TestRunEntryResponse.mode`). `department_id` в теле нет — кампания
привязывается к отделу инициатора (`identity.department_id`), не может быть
подделана в запросе.

`test_run_stands` теперь опционален. Заданный явно — прежнее поведение без
изменений: ровно эти стенды пула, тесты берутся по `pinned_stand_id` (группа
стенда, E3). Пустой/не заданный — состав кампании выводится из активного
состава СТП отдела для этого РЦ (полный прогон по РЦ): какие тесты, значит
какие стенды и ядра — решает СТП, оператор их не выбирает.

`debug` и `full` — независимые флаги, каждый допустим ровно в одном из двух
путей сборки состава:

- `debug` — только вместе с явным `test_run_stands` (групповой запуск
  стенда). Снимает и допуск по СТП, и требование статуса «Рабочий» для
  каждого теста — весь привязанный к стенду пул уходит в очередь как есть.
- `full` — только без `test_run_stands` (вывод из СТП). Перед сборкой
  состава запускает `/stp/generate` со `scope=full` для этого РЦ, поэтому
  состав кампании получается из уже расширенной СТП, а не только из того,
  что было сгенерировано раньше.

`TestRunCreateResponse` расширяет обычную карточку тремя списками —
`stands_without_tests` (стенд из пула без единого закреплённого теста, не
рушит остальную кампанию), `enqueue_errors` (частичные провалы постановки в
очередь одного конкретного теста одного стенда) и `stp_sync_errors`
(частичные провалы синхронизации СТП при `full=True`, per-стенд, отдельно от
`enqueue_errors` — то и другое не рушит остальную кампанию, см.
`services/test_run.py`).
"""

from datetime import datetime
from typing import Literal

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
        description="Официальный/финальный прогон релиза — чисто информационная метка, на допуск по СТП не влияет.",
    )
    debug: bool = Field(
        default=False,
        description=(
            "Допустим только вместе с явным test_run_stands. Снимает допуск по СТП и требование "
            "статуса «Рабочий» — весь пул стенда уходит в очередь без проверок."
        ),
    )
    full: bool = Field(
        default=False,
        description=(
            "Допустим только без test_run_stands (вывод из СТП). Перед сборкой состава расширяет "
            "СТП этого РЦ до полного набора (scope=full), затем запускает уже расширенный состав."
        ),
    )
    force: bool = Field(
        default=False,
        description=(
            "Запустить даже на занятых стендах кампании. Сервер сам перепроверяет право "
            "(department_admin отдела стенда либо роль admin testing_service в этом отделе) — "
            "у остальных вызывающих запрос на конкретный стенд отклоняется, а не тихо выполняется. "
            "На занятом человеком стенде реально отбирает бронь; `updating`/чужой `acs` не отбираются."
        ),
    )
    on_active_queue: Literal["append", "replace"] | None = Field(
        default=None,
        description=(
            "Режим для стендов, где очередь testing_service уже активна (одинаков для всех стендов "
            "кампании). Не задано — не-админ встаёт в конец очереди, у админа стенд попадает в "
            "enqueue_errors с STAND_QUEUE_ACTIVE. `append` — в конец очереди; `replace` — очистить "
            "остальные queued, прервать текущий тест и начать новые сразу (только админ отдела стенда)."
        ),
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
        description=(
            "launch — будет поставлен в очередь; skip_debug_required/skip_stand_inactive/"
            "skip_not_in_stp/skip_stp_not_generated — будет пропущен."
        ),
    )
    reason: str | None = None
    scenario_id: str | None = Field(
        default=None,
        description=(
            "При action=launch: тест-кейс запускается многостендовым сценарием (`ready`-сценарий отдела "
            "с этим stp_test_case_code), а не одиночным тестом на стенде."
        ),
    )
    stp_test_run_id: str | None = Field(
        default=None,
        description=(
            "Заполнен только при action=skip_not_in_stp — id уже существующего СТП-прогона этого "
            "контекста, куда можно добавить тест (POST /stp/test-runs/{id}/add-test). При "
            "skip_stp_not_generated СТП для этого контекста ещё не генерировалась — добавлять некуда."
        ),
    )


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
    details: dict | None = Field(
        default=None,
        description="Детали отказа (держатель стенда для STAND_BUSY, состав очереди для STAND_QUEUE_ACTIVE). Только в ответе на создание, при повторе по request_id не восстанавливаются.",
    )


class TestRunStpSyncError(BaseModel):
    """Один частичный провал синхронизации СТП (`/stp/generate`) при `full=True` —
    не про постановку в очередь, а про сам расширенный состав СТП стенда."""

    stand_id: str | None = None
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
    stp_sync_errors: list[TestRunStpSyncError] = Field(
        default_factory=list,
        description="Частичные провалы синхронизации СТП (только при full=True) — отдельно от enqueue_errors.",
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
    verdict: str | None = Field(
        default=None,
        description="Вердикт: passed/failed/unknown; unknown — результат не определён.",
    )
    zephyr_status_raw: str | None = Field(
        default=None, description="Статус тест-кейса в Zephyr как есть (последний прочитанный).",
    )


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
    scenario_run_id: str | None = Field(
        default=None,
        description="Запись запущена многостендовым сценарием (`GET /scenario-runs/{id}`), а не одиночным item'ом.",
    )


class TestRunDetailResponse(TestRunResponse):
    """Ответ GET /test-runs/{id} — карточка + все дочерние queue_items."""

    queue_items: list[TestRunQueueItemResponse] = Field(default_factory=list)
    entries: list[TestRunEntryResponse] = Field(default_factory=list)
    progress: dict[str, int] = Field(default_factory=dict)
