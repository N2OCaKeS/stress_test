"""Pydantic-схемы для §D8 — «Pull СТП из life» (прочитать/импортировать существующие
Zephyr test-run'ы отдела, читались бы они EMM или легаси-системой, или руками)."""

from pydantic import BaseModel, Field


class StpPullPreviewRequest(BaseModel):
    """Тело POST /stp/pull-from-life/preview. Только чтение — ни одной записи в БД."""

    os_version_id: str = Field(..., min_length=1, max_length=64, description="РЦ — та же папка Zephyr, что и /stp/generate.")
    department_id: str | None = Field(None, description="По умолчанию — отдел пользователя (чьи Jira-креды используются).")


class StpPullRunComposition(BaseModel):
    """Сводка состава одного найденного test-run'а."""

    case_count: int = Field(..., description="Сколько тест-кейсов в Zephyr test-run'е.")
    matched_case_count: int = Field(..., description="Из них уже есть локальным stp_test_case (по zephyr_id).")
    new_case_count: int = Field(..., description="Из них потребуют завести новый stp_test_case при импорте.")


class StpPullPreviewItem(BaseModel):
    """Один найденный в Zephyr test-run — как он будет сопоставлен/импортирован."""

    zephyr_key: str
    zephyr_link: str | None = Field(None, description="Лучшее приближение ссылки на Zephyr UI, не гарантирован формат.")
    name: str
    parsed_os_version_id: str | None = None
    parsed_mode: str | None = None
    parsed_kernel: str | None = None
    parsed_stand_token: str | None = None
    stand_id: str | None = Field(None, description="Локальный test_stands.id, если токен стенда сопоставился.")
    needs_manual_mapping: bool = Field(..., description="Имя не по конвенции или стенд не сопоставился — ручной разбор.")
    mapping_issue: str | None = Field(None, description="Код причины: NAME_NOT_PARSEABLE / STAND_NOT_FOUND / STAND_WRONG_DEPARTMENT.")
    already_imported: bool = Field(..., description="Уже есть локальный stp_test_run с этим zephyr_test_run_key.")
    stp_test_run_id: str | None = None
    composition: StpPullRunComposition


class StpPullPreviewResponse(BaseModel):
    """Ответ POST /stp/pull-from-life/preview."""

    department_id: str
    os_version_id: str
    folder: str
    items: list[StpPullPreviewItem] = Field(default_factory=list)
    total_found: int = 0
    new_count: int = 0
    already_imported_count: int = 0
    needs_manual_mapping_count: int = 0


class StpPullImportRequest(BaseModel):
    """Тело POST /stp/pull-from-life/import.

    `zephyr_keys` пуст/не задан — импортировать все тест-раны, найденные
    сейчас в папке (эквивалент «выбрать всё» в preview); список — только
    перечисленные ключи (остальные найденные просто не трогаются).
    """

    os_version_id: str = Field(..., min_length=1, max_length=64)
    department_id: str | None = Field(None, description="По умолчанию — отдел пользователя.")
    zephyr_keys: list[str] | None = Field(None, description="Пусто — импортировать все найденные в папке test-run'ы.")


class StpPullConflict(BaseModel):
    """Ячейка, чей локальный статус разошёлся со статусом Zephyr — НЕ перезаписана."""

    zephyr_key: str
    test_case_key: str
    stp_cell_id: str
    local_status: str
    zephyr_status: str


class StpPullRunResult(BaseModel):
    """Итог обработки одного test-run'а в рамках /stp/pull-from-life/import."""

    zephyr_key: str
    status: str = Field(..., description="succeeded / failed / skipped_needs_manual_mapping.")
    stp_test_run_id: str | None = None
    created_run: bool = False
    matched_run: bool = False
    cases_created: int = 0
    cases_matched: int = 0
    cells_created: int = 0
    cells_matched: int = 0
    conflicts: list[StpPullConflict] = Field(default_factory=list)
    mapping_issue: str | None = None
    error: str | None = None


class StpPullImportResponse(BaseModel):
    """Ответ POST /stp/pull-from-life/import — итог по каждому обработанному test-run'у + агрегаты."""

    department_id: str
    os_version_id: str
    results: list[StpPullRunResult] = Field(default_factory=list)
    created_runs: int = 0
    matched_runs: int = 0
    cases_created: int = 0
    cases_matched: int = 0
    cells_created: int = 0
    cells_matched: int = 0
    conflicts_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
