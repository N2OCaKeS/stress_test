"""Pydantic-схемы `/test-definitions/{test_id}/steps`."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.core.constants import StepRunMode
from src.schemas.test_definition import StandSetup


class TestStepCreate(BaseModel):
    """Тело POST /test-definitions/{test_id}/steps — новый шаг в конец (или на `position`)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=128, description="Подпись шага в UI и логе (например, «maxcpus=16»).")
    starter_suffix: str | None = Field(
        default=None, max_length=16,
        description="Позиционный $5 у starter.sh/скрипта повторного запуска (kernel/balance/oom); пусто — без флага.",
    )
    run_mode: StepRunMode = Field(
        default=StepRunMode.RERUN,
        description=(
            "full — команда запуска профиля (starter.sh с клонированием ветки); "
            "rerun — повторный запуск уже склонированного кода (скрипт повторного запуска профиля)."
        ),
    )
    stand_setup: StandSetup | None = Field(
        default=None,
        description=(
            "Настройка стенда перед шагом: у первого шага — в prepare-for-test, у остальных — "
            "операцией «настройка без restore». Пусто — шаг идёт сразу за предыдущим."
        ),
    )
    position: int | None = Field(default=None, ge=0, description="Позиция; пусто — в конец.")
    copy_args_from_step_id: str | None = Field(
        default=None, max_length=64,
        description="Скопировать слоты команды из другого шага этого теста (правка потом — по слотам).",
    )


class TestStepUpdate(BaseModel):
    """Тело PATCH /test-definitions/{test_id}/steps/{step_id}. Все поля необязательны."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=128)
    starter_suffix: str | None = Field(default=None, max_length=16)
    run_mode: StepRunMode | None = None
    stand_setup: StandSetup | None = Field(default=None, description="Заменить настройку стенда; null — убрать.")


class TestStepOrder(BaseModel):
    """Тело PUT /test-definitions/{test_id}/steps/order — все шаги теста в новом порядке."""

    model_config = ConfigDict(extra="forbid")

    step_ids: list[str] = Field(min_length=1, max_length=100)


class TestStepResponse(BaseModel):
    """Шаг теста в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Step ID (prefix tstep_).")
    test_id: str
    position: int
    name: str
    starter_suffix: str | None = None
    run_mode: StepRunMode
    stand_setup: StandSetup | None = None
    created_at: datetime
    updated_at: datetime
