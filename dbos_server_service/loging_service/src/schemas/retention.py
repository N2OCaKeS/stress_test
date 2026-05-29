"""Схемы для управления retention-политикой."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.utils.normalization import normalize_service_name

# Тот же charset, что и у `EventCreate.service` после `normalize_service_name`
# (`schemas/events.py::_SERVICE_PATTERN`). Иначе политика с uppercase /
# digits / `-` тихо не матчит ни одного события: ingest хранит `[a-z_]`,
# а фильтр пускал `[A-Za-z0-9._-]` и оседал в БД с именами, которых там
# никогда не будет.
_SERVICE_FILTER_PATTERN: re.Pattern[str] = re.compile(r"^[a-z_]{1,64}$")

# Имя сервиса, события которого защищены от ротации (`apply_active` форсит
# `service != _PROTECTED_SERVICE`). Политика с этим сервисом в фильтре
# никогда не сработает, поэтому ловим её на schema-уровне и возвращаем 422,
# а не молча создаём dead-row, который вводит оператора в заблуждение.
_PROTECTED_SERVICE = "loging_service"

# Severity-уровни синхронизированы с `schemas/events.py::EventCreate.severity`
# — единый whitelist по всему сервису. Любая правка одного — править оба.
Severity = Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _normalise_filter(values: list[str] | None) -> list[str] | None:
    """Дедупликация + сортировка фильтра. None / пустой список → None.

    Пустой `[]` от клиента эквивалентен «нет фильтра» — иначе пользователь
    создал бы политику, не матчащую ни одного события и retain_days =
    бесконечности, что эквивалентно отсутствию политики, но засоряет
    таблицу.
    """
    if not values:
        return None
    return sorted(set(values))


class RetentionPolicyCreate(BaseModel):
    retain_days: int = Field(
        ge=30,
        le=3650,
        description=(
            "Сколько дней хранить ВСЕ события аудита (минимум 30). "
            "События `loging_service` всегда защищены от удаления."
        ),
    )
    description: str | None = Field(default=None, max_length=256)
    is_active: bool = Field(default=True)

    # severity_filter / service_filter — резервные поля, заведённые миграцией
    # `g7b8c9d0e1f2`. None или пустой список = политика применяется ко ВСЕМ
    # severity / ВСЕМ сервисам. Список — Cartesian expansion: одна строка на
    # каждую пару (severity_i, service_j) с retain_days.
    severity_filter: list[Severity] | None = Field(
        default=None,
        max_length=6,
        description=(
            "Какие severity-уровни охватывает политика. None / [] = все."
        ),
    )
    service_filter: list[str] | None = Field(
        default=None,
        max_length=64,
        description=(
            "Какие сервисы охватывает политика. None / [] = все. "
            "Имена `loging_service` принимаются, но retention для них не "
            "применяется — событийный audit-trail защищён от ротации."
        ),
    )

    @field_validator("severity_filter", mode="after")
    @classmethod
    def _dedupe_severity(cls, v: list[str] | None) -> list[str] | None:
        return _normalise_filter(v)

    @field_validator("service_filter", mode="after")
    @classmethod
    def _validate_services(cls, v: list[str] | None) -> list[str] | None:
        if not v:
            return None
        # Сначала прогоняем каждое имя через ту же security-нормализацию,
        # что и `EventCreate.service` (NFKC + invisibles + confusables +
        # lower). Затем чарсет — `[a-z_]{1,64}`. Совпадает с регексом
        # ingest'а, поэтому политика не может тихо застрять с именем,
        # под которое события никогда не попадут.
        canonical: list[str] = []
        for name in v:
            if not isinstance(name, str):
                raise ValueError("service name must be a string")
            normalised = normalize_service_name(name)
            if not _SERVICE_FILTER_PATTERN.match(normalised):
                raise ValueError(
                    f"service name {name!r} invalid after normalisation; "
                    "allowed: [a-z_]{1,64} (snake_case, без digits/Unicode)"
                )
            if normalised == _PROTECTED_SERVICE:
                raise ValueError(
                    f"service {_PROTECTED_SERVICE!r} is protected from retention "
                    "and cannot appear in service_filter"
                )
            canonical.append(normalised)
        return sorted(set(canonical))


class RetentionPolicyResponse(BaseModel):
    id: str
    retain_days: int
    description: str | None
    is_active: bool
    severity: str | None = None
    service: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
