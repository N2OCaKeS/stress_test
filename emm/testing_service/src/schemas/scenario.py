"""Pydantic-схемы `/scenarios` — многостендовые сценарии.

Сценарий редактируется целиком: `PUT` заменяет поля, стенды и действия
одним документом. Действия ссылаются на стенд сценария по `stand_id` стенда
пула — стенд в сценарии не больше одного раза, так ссылка однозначна и
не требует id строк `scenario_stands` у клиента.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.constants import TestMode, TestReadiness
from src.schemas.launch_preview import LaunchPreviewError, LaunchPreviewResponse
from src.schemas.test_definition import StandSetup, _validate_code

ScenarioActionKind = Literal["run_test", "prepare_stand", "wait"]
ScenarioPreparation = Literal["full", "revert_only", "none"]


class ScenarioStandIn(BaseModel):
    stand_id: str = Field(min_length=1, max_length=64, description="Стенд пула (`test_stands.id`): сервер или ВМ.")
    label: str | None = Field(default=None, max_length=128, description="Подпись для UI (КД, клиент, …).")
    preparation: ScenarioPreparation = Field(
        default="full", description="full — restore + подготовка; revert_only — только откат; none — как есть.",
    )
    skip_pam_fix: bool = Field(
        default=False,
        description="Не выполнять PAM-правку (`pam_lastlog.so inactive=`) при подготовке, независимо от "
                    "профиля подготовки и `preparation`.",
    )
    provisioning_profile_id: str | None = Field(default=None, max_length=64)
    stand_setup: StandSetup | None = Field(default=None, description="Шаг настройки стенда.")
    kernel_override: str | None = Field(default=None, max_length=128, description="Ядро стенда вместо ядра запуска.")
    mode_override: TestMode | None = Field(default=None, description="Режим стенда вместо режима теста.")

    @field_validator("label", "kernel_override", "provisioning_profile_id")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ScenarioActionIn(BaseModel):
    kind: ScenarioActionKind
    stand_id: str | None = Field(
        default=None, max_length=64, description="Стенд сценария (id стенда пула) для run_test и prepare_stand.",
    )
    test_id: str | None = Field(default=None, max_length=64, description="Тест для run_test.")
    is_verdict: bool = Field(default=False, description="Вердикт сценария берётся из действий с этим флагом.")
    params: dict[str, Any] = Field(default_factory=dict, description="wait — `{\"seconds\": N}`.")


class ScenarioWrite(BaseModel):
    """Тело POST и PUT: сценарий целиком."""

    code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    department_id: str = Field(min_length=1, max_length=64)
    readiness: TestReadiness = TestReadiness.DEVELOPMENT
    stp_test_case_code: str | None = Field(
        default=None, max_length=128,
        description=(
            "Тест-кейс СТП, который запускает этот сценарий: кампания по СТП ставит сценарий вместо "
            "одиночного теста, вердикт пишется в ячейку кейса. Нужен ровно один is_verdict. "
            "В PUT не передан — остаётся прежним; null — снять связь."
        ),
    )
    stands: list[ScenarioStandIn] = Field(default_factory=list, max_length=32)
    actions: list[ScenarioActionIn] = Field(default_factory=list, max_length=200)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _validate_code(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()

    @field_validator("stp_test_case_code")
    @classmethod
    def _stp_code(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _validate_code(value)


class ScenarioStandOut(ScenarioStandIn):
    stand_setup: dict[str, Any] | None = None
    id: str
    target_type: str = Field(description="server | vm — из стенда пула.")
    stand_name: str | None = Field(default=None, description="legacy_token стенда, если есть.")


class ScenarioActionOut(ScenarioActionIn):
    id: str
    position: int
    test_code: str | None = None


class ScenarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    department_id: str
    readiness: str
    stp_test_case_code: str | None = None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    stands: list[ScenarioStandOut] = Field(default_factory=list)
    actions: list[ScenarioActionOut] = Field(default_factory=list)


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    department_id: str
    readiness: str
    stp_test_case_code: str | None = None
    stands_count: int
    actions_count: int
    updated_at: datetime


class ScenarioListResponse(BaseModel):
    items: list[ScenarioSummary]


class ScenarioPreviewRequest(BaseModel):
    os_version_id: str = Field(min_length=1, max_length=64, description="Версия ОС (РЦ).")
    kernel: str = Field(min_length=1, max_length=128, description="Ядро; `kernel_override` стенда главнее.")
    debug: bool = False


class ScenarioActionPreview(BaseModel):
    position: int
    kind: ScenarioActionKind
    stand_id: str | None = None
    test_id: str | None = None
    is_verdict: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    launch: LaunchPreviewResponse | None = Field(
        default=None, description="run_test — превью запуска теста на стенде действия (как).",
    )
    stand: dict[str, Any] | None = Field(
        default=None, description="prepare_stand — подготовка стенда: preparation, ядро, режим, настройка.",
    )
    errors: list[LaunchPreviewError] = Field(default_factory=list)


class ScenarioPreviewResponse(BaseModel):
    scenario_id: str
    actions: list[ScenarioActionPreview]


# ── запуск ───────────────────────────────────────────────────────────

class ScenarioRunRequest(BaseModel):
    os_version_id: str = Field(min_length=1, max_length=64, description="Версия ОС (РЦ).")
    kernel: str = Field(min_length=1, max_length=128, description="Ядро; `kernel_override` стенда главнее.")
    mode: TestMode = Field(default=TestMode.OREL, description="Режим; `mode_override` стенда главнее.")
    debug: bool = Field(default=False, description="Debug-запуск: допускает сценарий и тесты не в `ready`.")
    stp_test_run_id: str | None = Field(
        default=None, max_length=64,
        description=(
            "Запуск из ячейки СТП: столбец СТП (`stp_test_runs.id`), в ячейку которого пишется вердикт. "
            "Проверяется так же, как обычный запуск по СТП (права на стенд КД + членство кейса "
            "`stp_test_case_code` в этом столбце). Несовместим с debug."
        ),
    )


class ScenarioRunStandOut(BaseModel):
    stand_id: str
    label: str | None = None
    preparation: str
    skip_pam_fix: bool = False
    kernel: str
    mode: str
    state: str = Field(description="pending | acquiring | preparing | ready | failed | released.")
    error: str | None = None


class ScenarioRunActionOut(BaseModel):
    position: int
    kind: ScenarioActionKind
    stand_id: str | None = None
    test_id: str | None = None
    is_verdict: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    queue_item_id: str | None = Field(default=None, description="Item действия run_test (логи, вердикт).")
    state: str | None = None
    verdict: str | None = None


class ScenarioRunResponse(BaseModel):
    id: str
    scenario_id: str
    department_id: str
    state: str = Field(
        description="waiting_for_stands | preparing | running | stopping | succeeded | failed | stopped.",
    )
    launch_context: dict[str, Any]
    debug_mode: bool
    stp_test_run_id: str | None = Field(default=None, description="Столбец СТП, в ячейку которого пишется вердикт.")
    test_run_id: str | None = Field(default=None, description="Кампания, из которой запущен сценарий.")
    current_position: int | None = None
    wait_until: datetime | None = None
    blocked_by: list[dict[str, Any]] = Field(default_factory=list, description="Занятые стенды при ожидании.")
    verdict: str | None = None
    error: str | None = None
    created_by: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stands: list[ScenarioRunStandOut] = Field(default_factory=list)
    actions: list[ScenarioRunActionOut] = Field(default_factory=list)


class ScenarioRunListResponse(BaseModel):
    items: list[ScenarioRunResponse]

