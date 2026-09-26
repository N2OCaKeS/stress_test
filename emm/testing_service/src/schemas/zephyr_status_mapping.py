"""Pydantic-схемы `/zephyr-status-mappings`."""

from pydantic import BaseModel, Field, field_validator

from src.core.constants import VerdictOutcome


class ZephyrStatusMappingItem(BaseModel):
    """Одна строка маппинга."""

    zephyr_status: str = Field(
        ..., min_length=1, max_length=64,
        description=(
            "Статус тест-кейса в Zephyr как его отдаёт API: имя статуса ATM "
            "(\"Pass\") или числовой id (\"91\"). Сравнивается без учёта регистра."
        ),
    )
    outcome: VerdictOutcome = Field(
        description="passed — тест пройден; failed — провален; not_finished — ждать дальше.",
    )

    @field_validator("zephyr_status")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("zephyr_status must not be blank")
        return stripped


class ZephyrStatusMappingUpdate(BaseModel):
    """Тело PUT: заменяет набор отдела целиком."""

    items: list[ZephyrStatusMappingItem] = Field(..., min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def _unique(cls, value: list[ZephyrStatusMappingItem]) -> list[ZephyrStatusMappingItem]:
        keys = [item.zephyr_status.casefold() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("zephyr_status values must be unique (case-insensitive)")
        return value


class ZephyrStatusMappingResponse(BaseModel):
    """Действующий маппинг отдела."""

    department_id: str = Field(description="Отдел.")
    is_default: bool = Field(
        description="true — у отдела своих строк нет, действует набор по умолчанию.",
    )
    items: list[ZephyrStatusMappingItem] = Field(description="Строки маппинга.")
