"""Схемы запросов/ответов для правил аудита."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# `DROP` принимается как входной alias к `SUPPRESS` (модель безопасности
# §logging.3 называет эффект именно DROP). Внутри сервиса, в БД и в ответах
# всегда хранится канонический `SUPPRESS` — alias нормализуется в
# model_validator'ах ниже.
RuleEffect = Literal["SUPPRESS", "DROP", "ALLOW", "OVERRIDE_SEVERITY"]
# `warning` симметрично `EventCreate.status` — soft-mode гарды
# (server_service::internal_service._check_target_department, audit от
# internal-dept) пишут события со `status="warning"`. Без него
# `match_status="warning"` в правиле отбивался валидатором, и админ не мог
# создать SUPPRESS/OVERRIDE правило для warning-событий.
RuleStatus = Literal["success", "failure", "denied", "warning"]
RuleSeverity = Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class RuleCreate(BaseModel):
    name: str = Field(max_length=128, description="Уникальное имя правила")
    description: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    priority: int = Field(default=100, ge=1, le=1000, description="Приоритет (выше = выполняется первым)")

    # Критерии совпадения — None означает «любое значение»
    match_service: str | None = Field(default=None, max_length=64, description="Точное имя сервиса")
    match_action: str | None = Field(
        default=None, max_length=128,
        description="Точное имя или glob-паттерн: 'user.*', 'http.*'",
    )
    match_status: RuleStatus | None = Field(default=None)
    match_severity: RuleSeverity | None = Field(default=None)
    match_allowed: bool | None = Field(default=None)

    # Эффект. `DROP` принимается как alias к `SUPPRESS`.
    effect: RuleEffect
    effect_severity: RuleSeverity | None = Field(
        default=None,
        description="Обязательно для effect=OVERRIDE_SEVERITY",
    )

    @model_validator(mode="after")
    def _validate_effect(self) -> "RuleCreate":
        if self.effect == "DROP":
            self.effect = "SUPPRESS"
        if self.effect == "OVERRIDE_SEVERITY" and not self.effect_severity:
            raise ValueError("effect_severity обязателен при effect=OVERRIDE_SEVERITY")
        if self.effect != "OVERRIDE_SEVERITY" and self.effect_severity:
            raise ValueError("effect_severity используется только с effect=OVERRIDE_SEVERITY")
        return self


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None)
    is_active: bool | None = Field(default=None)
    priority: int | None = Field(default=None, ge=1, le=1000)
    match_service: str | None = Field(default=None, max_length=64)
    match_action: str | None = Field(default=None, max_length=128)
    match_status: RuleStatus | None = Field(default=None)
    match_severity: RuleSeverity | None = Field(default=None)
    match_allowed: bool | None = Field(default=None)
    effect: RuleEffect | None = Field(default=None)
    effect_severity: RuleSeverity | None = Field(default=None)

    @model_validator(mode="after")
    def _normalize_effect(self) -> "RuleUpdate":
        if self.effect == "DROP":
            self.effect = "SUPPRESS"
        return self


class RuleResponse(BaseModel):
    id: str
    name: str
    description: str | None
    is_active: bool
    priority: int
    match_service: str | None
    match_action: str | None
    match_status: str | None
    match_severity: str | None
    match_allowed: bool | None
    effect: str
    effect_severity: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RuleListResponse(BaseModel):
    items: list[RuleResponse]
    total: int
    limit: int
    offset: int
