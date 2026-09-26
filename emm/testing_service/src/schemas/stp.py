"""Pydantic-схемы СТП (§2.5, §6 плана миграции): тест-кейсы, генерация, прогоны, ячейки."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.zephyr_folder import ZephyrFolderResponse


class StpTestCaseCreate(BaseModel):
    """Тело POST /stp/test-cases. `code` уникален (совпадает с test_definitions.code)."""

    code: str = Field(..., min_length=1, max_length=64, description="Код теста (join-ключ с test_definitions.code).")
    title: str = Field(..., min_length=1, max_length=256, description="Название тест-кейса.")
    zephyr_id: str | None = Field(
        default=None, max_length=32,
        description="Ключ тест-кейса в Zephyr (BT-Txxxx). Заполняется оператором после создания в Zephyr UI.",
    )
    department_id: str | None = Field(default=None, description="Отдел-владелец тест-кейса.")


class StpTestCaseUpdate(BaseModel):
    """Тело PATCH /stp/test-cases/{id}. Все поля опциональны."""

    title: str | None = Field(default=None, min_length=1, max_length=256)
    zephyr_id: str | None = Field(default=None, max_length=32)
    department_id: str | None = Field(default=None)


class StpTestCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    title: str
    zephyr_id: str | None = None
    department_id: str | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None


class StpGenerateRequest(BaseModel):
    """Тело POST /stp/generate — админский запуск/переключение состава СТП (§5, §D4/D5).

    `scope` — явный выбор («Полный набор»/«По changelog»), НЕ вычисляется из
    вида RC (§D4). Повтор с тем же `scope` — идемпотентный no-op (можно
    использовать, чтобы досоздать прогоны для новых стендов). Повтор с ДРУГИМ
    `scope` для уже существующей пары (department, os_version_id) —
    переключение активного состава: недостающие тесты добавляются в
    существующий Zephyr test-run, выпавшие — деактивируются локально
    (`stp_cells.is_active=false`), без потери статуса/истории.
    """

    os_version_id: str = Field(..., min_length=1, max_length=64, description="РЦ (тот же id, что и test_runs.os_version_id).")
    mode: str | None = Field(None, min_length=1, max_length=16, description="Без значения — все режимы.")
    kernel: str | None = Field(None, min_length=1, max_length=64, description="Без значения — все ядра из репозиториев ОС.")
    scope: str = Field(..., description="Один из: changelog/full (см. StpCompositionScope).")
    department_id: str | None = Field(None, description="По умолчанию — отдел пользователя.")


class StpGeneratePartialError(BaseModel):
    """Частичный провал генерации СТП для одного стенда — остальные не пострадали."""

    stand_id: str
    error_code: str
    message: str


class StpTestRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    os_version_id: str
    mode: str
    kernel: str
    stand_id: str
    zephyr_test_run_key: str | None = None
    zephyr_folder_path: str | None = None
    created_at: datetime
    updated_at: datetime


class StpGenerateResponse(BaseModel):
    """Ответ POST /stp/generate — заведённые прогоны + частичные ошибки по стендам.

    `zephyr_folder` — папка Zephyr этой РЦ: путь, id и `error`, если
    id получить не удалось (прогоны при этом всё равно заводятся).
    """

    test_runs: list[StpTestRunResponse] = Field(default_factory=list)
    errors: list[StpGeneratePartialError] = Field(default_factory=list)
    zephyr_folder: ZephyrFolderResponse | None = None


class StpCellResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    stp_test_case_id: str
    stp_test_run_id: str
    status: str
    is_active: bool = True
    queue_item_id: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class StpCompositionResponse(BaseModel):
    """Ответ GET /stp/composition — текущий активный `scope`+`revision` пары
    `(department_id, os_version_id)` (§D4/D5). Отсутствие строки — не 404, а
    дефолт `scope: null, revision: 0` (состав ещё ни разу не генерировался),
    тот же паттерн, что и `DepartmentIntegrationSettingsResponse`."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    department_id: str
    os_version_id: str
    scope: str | None = None
    revision: int = 0
    updated_at: datetime | None = None
    updated_by: str | None = None


class StpCellManualUpdate(BaseModel):
    """Тело PATCH /stp/cells/{id} — ручной override статуса. Не трогает Zephyr."""

    status: str = Field(..., description="Один из: not_run/in_progress/pass/fail.")


class StpMatrixPublishRequest(BaseModel):
    """Тело POST /stp/matrix/publish — ручная публикация сводной СТП-таблицы одного РЦ."""

    os_version_id: str = Field(..., min_length=1, max_length=64, description="РЦ (тот же id, что и stp_test_runs.os_version_id).")
    department_id: str | None = Field(None, description="По умолчанию — отдел пользователя.")


class StpMatrixPublishResponse(BaseModel):
    """Ответ POST /stp/matrix/publish — исход публикации, см. `StpMatrixPublicationStatus`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    department_id: str
    os_version_id: str
    status: str
    confluence_page_id: str | None = None
    confluence_parent_page_id: str | None = None
    error: str | None = None
    published_at: datetime | None = None
    updated_at: datetime
