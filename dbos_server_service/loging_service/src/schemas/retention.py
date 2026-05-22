"""Схемы для управления retention-политикой."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
        normalised = _normalise_filter(v)
        if normalised is None:
            return None
        # Charset гард на каждое имя — тот же что у ingest, чтобы фильтр
        # не пускал Unicode-confusables / ascii-control мимо retention'а.
        # 64 — `String(64)` predикат на колонку модели.
        for name in normalised:
            if not name or len(name) > 64:
                raise ValueError(
                    "service name length must be 1..64 chars"
                )
            if not all(c.isalnum() or c in "._-" for c in name):
                raise ValueError(
                    f"service name {name!r} has invalid chars; "
                    "allowed: [A-Za-z0-9._-]"
                )
        return normalised


class RetentionPolicyUpdate(BaseModel):
    retain_days: int | None = Field(default=None, ge=30, le=3650)
    description: str | None = None
    is_active: bool | None = None


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
