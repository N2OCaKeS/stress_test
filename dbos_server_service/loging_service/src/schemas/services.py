"""Схемы для эндпоинтов /services."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Сводка по сервису (из audit_events) ──────────────────────────────────────

class ServiceInfo(BaseModel):
    service: str
    event_count: int
    last_event_at: datetime


class ServiceListResponse(BaseModel):
    items: list[ServiceInfo]
    total: int


# ── Реестр событий сервиса ────────────────────────────────────────────────────

class EventDefinition(BaseModel):
    """Описание одного события, которое сервис заявляет, что может эмитить."""

    action: str = Field(
        max_length=128,
        description="Action в dot-namespace, напр. 'user.login'",
    )
    description: str | None = Field(
        default=None,
        max_length=256,
        description="Человекочитаемое описание: когда это событие происходит",
    )
    # Whitelist. Без него `default_severity="ROFL"` валидно проходит,
    # потом всплывает в `_DEFAULT_SEVERITY` lookup'е как тихий no-op.
    default_severity: Literal[
        "TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    ] | None = Field(
        default=None,
        description="Дефолтная severity (TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL)",
    )


class RegisterEventsRequest(BaseModel):
    events: list[EventDefinition] = Field(
        min_length=1,
        max_length=500,
        description="Полный список событий, которые сервис может эмитить",
    )


class RegisterEventsResponse(BaseModel):
    service: str
    added: int
    updated: int
    total: int


class ServiceEventDetail(BaseModel):
    """Зарегистрированное событие как оно хранится в `service_events`."""

    action: str
    description: str | None
    # Хранение — `String(16)` (плюс legacy NULL'ы), а сам тип эхошен read-only.
    # На read'е оставляем `str | None`: если в БД лежит legacy-значение от
    # старого валидатора, не хочется ломать `GET /services/{svc}/events`.
    default_severity: str | None
    registered_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ServiceEventsResponse(BaseModel):
    service: str
    items: list[ServiceEventDetail]
    total: int
    limit: int
    offset: int
