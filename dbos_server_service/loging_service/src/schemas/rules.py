"""Схемы запросов/ответов для правил аудита."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.utils.normalization import normalize_service_name_preserve_case

# Тот же charset, что у `EventCreate.service`: snake_case ASCII после NFKC.
# Без симметрии правило с `match_service="Auth-Service"` или
# `match_service="auth_service​"` (с zero-width space) молча не матчит
# нормализованные на ingest event.service — SUPPRESS/OVERRIDE не работает.
_RULE_SERVICE_PATTERN: re.Pattern[str] = re.compile(r"^[a-z_]{1,64}$")

# Симметричен `EventCreate.action` (`[a-z0-9_.]{1,128}`), плюс `*` для glob —
# rule-движок матчит `match_action="user.*"`. CRLF/Unicode в имени или glob'е
# уехали бы в admin-list endpoint и оттуда в JSON-логи / CSV — log-injection
# при отображении правил.
_RULE_ACTION_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9_.*]{1,128}$")

# Rule.name отдаётся клиентам as-is (UI/CSV/JSON), CRLF в имени подделывает
# вторую строку в log-shipping pipeline. Печатные ASCII без CR/LF/TAB.
_RULE_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[\x20-\x7E]{1,128}$")

# `description` идёт в admin-UI и в CSV-экспорт правил. Сам name отбит
# `_RULE_NAME_PATTERN`, но description раньше принимал любые символы —
# включая `\r`, `\n`, NUL. CRLF в описании раскалывает строку CSV/JSON
# при экспорте и подделывает вторую строку в log-shipping pipeline тем же
# образом, что и `actor_id` / `action` в `EventCreate` (см. schemas/events.py).
# Пускаем юникод-text (description содержательнее name), но запрещаем
# control-байты: CR, LF, TAB, NUL и весь C0/C1 кроме обычного пробела.
_RULE_DESCRIPTION_CONTROL_RE: re.Pattern[str] = re.compile(
    r"[\x00-\x1f\x7f]"
)


def _validate_rule_description(value: str | None) -> str | None:
    if value is None:
        return None
    if _RULE_DESCRIPTION_CONTROL_RE.search(value):
        raise ValueError(
            "description must not contain control characters "
            "(CR, LF, TAB, NUL, other C0/C1)"
        )
    return value


def _normalize_match_service(value: str | None) -> str | None:
    """NFKC + invisibles/confusables + charset-проверка `[a-z_]{1,64}`.

    Идёт через preserve-case вариант, потому что pattern `[a-z_]` сам обязан
    отбивать uppercase: иначе `match_service="AUTH_SERVICE"` молча свернулся бы
    в `auth_service` и тихо нарушил bijection с `EventCreate.service` (тоже
    `[a-z_]`). Финальный `.lower()` тут излишен — pattern уже гарантирует
    lower-case ASCII, — поэтому возвращаем normalized как есть.
    """
    if value is None:
        return None
    normalized = normalize_service_name_preserve_case(value)
    if not _RULE_SERVICE_PATTERN.match(normalized):
        raise ValueError(
            "match_service must match [a-z_]{1,64} after NFKC normalisation "
            "(lowercase snake_case, symmetric with EventCreate.service)"
        )
    return normalized


def _validate_match_action(value: str | None) -> str | None:
    if value is None:
        return None
    if not _RULE_ACTION_PATTERN.match(value):
        raise ValueError(
            "match_action must match [a-z0-9_.*]{1,128} "
            "(no CR/LF, no uppercase, no Unicode; '*' allowed for glob)"
        )
    return value


def _validate_rule_name(value: str) -> str:
    if not _RULE_NAME_PATTERN.match(value):
        raise ValueError(
            "name must be printable ASCII (no CR/LF/TAB), 1..128 chars"
        )
    return value


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
    # В БД description хранится как Text без явного limit'а; общий потолок
    # тела запроса 1 MiB. Явный cap в схеме делает контракт очевидным и
    # отбивает мусорные payload'ы до создания row'у.
    description: str | None = Field(default=None, max_length=1024)
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

    @field_validator("match_service", mode="after")
    @classmethod
    def _normalize_match_service(cls, v: str | None) -> str | None:
        return _normalize_match_service(v)

    @field_validator("match_action", mode="after")
    @classmethod
    def _validate_match_action(cls, v: str | None) -> str | None:
        return _validate_match_action(v)

    @field_validator("name", mode="after")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _validate_rule_name(v)

    @field_validator("description", mode="after")
    @classmethod
    def _validate_description(cls, v: str | None) -> str | None:
        return _validate_rule_description(v)

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
    description: str | None = Field(default=None, max_length=1024)
    is_active: bool | None = Field(default=None)
    priority: int | None = Field(default=None, ge=1, le=1000)
    match_service: str | None = Field(default=None, max_length=64)
    match_action: str | None = Field(default=None, max_length=128)
    match_status: RuleStatus | None = Field(default=None)
    match_severity: RuleSeverity | None = Field(default=None)
    match_allowed: bool | None = Field(default=None)
    effect: RuleEffect | None = Field(default=None)
    effect_severity: RuleSeverity | None = Field(default=None)

    @field_validator("match_service", mode="after")
    @classmethod
    def _normalize_match_service(cls, v: str | None) -> str | None:
        return _normalize_match_service(v)

    @field_validator("match_action", mode="after")
    @classmethod
    def _validate_match_action(cls, v: str | None) -> str | None:
        return _validate_match_action(v)

    @field_validator("name", mode="after")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_rule_name(v)

    @field_validator("description", mode="after")
    @classmethod
    def _validate_description(cls, v: str | None) -> str | None:
        return _validate_rule_description(v)

    @model_validator(mode="after")
    def _normalize_effect(self) -> "RuleUpdate":
        if self.effect == "DROP":
            self.effect = "SUPPRESS"
        # PATCH-семантика: финальный invariant `effect ↔ effect_severity`
        # проверяется в endpoint'е `update_rule` после мёржа с DB-состоянием
        # (одно из полей может остаться от прежнего значения). Здесь только
        # очевидная клиентская ошибка — оба поля в payload одновременно,
        # и они несовместимы.
        eff_set = "effect" in self.model_fields_set
        sev_set = "effect_severity" in self.model_fields_set
        if eff_set and sev_set:
            if self.effect == "OVERRIDE_SEVERITY" and self.effect_severity is None:
                raise ValueError(
                    "effect_severity обязателен при effect=OVERRIDE_SEVERITY"
                )
            if self.effect != "OVERRIDE_SEVERITY" and self.effect_severity is not None:
                raise ValueError(
                    "effect_severity используется только с effect=OVERRIDE_SEVERITY"
                )
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
    """Постраничный список правил.

    Shape канонический — items+has_more+limit+offset+total. На таблице
    `audit_rules` row'ей мало, поэтому `total` считается всегда. `has_more`
    дублирует признак следующей страницы — выставляется через сравнение
    `offset + len(items) < total`, без второго прохода по БД.
    """

    items: list[RuleResponse]
    total: int
    has_more: bool = False
    limit: int
    offset: int
