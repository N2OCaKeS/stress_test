"""Схемы для эндпоинтов /services."""

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from src.core.constants import Severity
from src.utils.normalization import normalize_identifier


# ── Сводка по сервису (из audit_events) ──────────────────────────────────────

class ServiceInfo(BaseModel):
    service: str
    event_count: int
    last_event_at: datetime


class ServiceListResponse(BaseModel):
    """Список сервисов, у которых есть события.

    Shape выровнен с `EventListResponse` / `RuleListResponse` / `ServiceEventsResponse`:
    items+has_more+limit+offset+total. Реальной пагинации тут нет — список
    заведомо короткий (десятки сервисов на платформе), поэтому `has_more`
    всегда `False`, а `limit`/`offset` `None`. `total = len(items)` — без
    отдельного COUNT'а по журналу.
    """

    items: list[ServiceInfo]
    total: int
    has_more: bool = False
    limit: int | None = None
    offset: int | None = None


# ── Реестр событий сервиса ────────────────────────────────────────────────────

# Тот же charset, что у `EventCreate.action` в schemas/events.py. Каталоговый
# путь (`POST /services/{service}/events`) пишет action в `service_events`,
# откуда он потом светится в admin-UI и сравнивается с `audit_events.action`
# на JOIN'ах. Без charset'а атакующий с SERVICE_API_KEY мог бы залить
# `action="user.login\r\n[ALERT] fake"` в реестр (log-injection в downstream
# CSV/SIEM-экспортах) или кириллический homoglyph (`u` U+0443) и подделать
# реестр под чужой сервис без срабатывания фильтров.
_ACTION_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9_.]{1,128}$")

# `description` рефлектится в admin-UI и в audit-event'е `logging.service_events_
# registered` через target_type. Запрещаем CRLF / NUL / control-chars; обычные
# Unicode-буквы и пробелы оставляем — это человекочитаемое поле.
_DESCRIPTION_CONTROL_RE: re.Pattern[str] = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


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
    default_severity: Severity | None = Field(
        default=None,
        description="Дефолтная severity (TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL)",
    )

    @field_validator("action")
    @classmethod
    def _action_charset(cls, v: str) -> str:
        # Симметрия с `EventCreate._action_charset`: NFKC + invisibles strip +
        # confusables fold сначала, потом charset. Без normalize кириллическая
        # `u` (U+0443) или ZWSP-padding между точками тихо проходили бы regex
        # и попадали в реестр.
        normalised = normalize_identifier(v)
        if not _ACTION_PATTERN.match(normalised):
            raise ValueError(
                "action must match [a-z0-9_.]{1,128} after NFKC normalisation "
                "(no CR/LF, no uppercase, no Unicode homoglyphs)"
            )
        return normalised

    @field_validator("description")
    @classmethod
    def _description_no_control_chars(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if _DESCRIPTION_CONTROL_RE.search(v):
            raise ValueError(
                "description must not contain CR/LF/NUL or other control characters"
            )
        return v


class RegisterEventsRequest(BaseModel):
    # Лимит 500 действий × ~500 байт описания = ~250 кБ, что укладывается в
    # `MAX_REQUEST_BODY_BYTES` (1 МБ по умолчанию). Два предела работают
    # независимо: schema-валидатор отбивает кардинальность, middleware —
    # raw-байтовый объём. Менять один — не забыть пересчитать другой.
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
    """Постраничный каталог зарегистрированных action'ов сервиса.

    Shape канонический — items+has_more+limit+offset+total. `total` берётся
    через COUNT, `has_more` сравнивает `offset + len(items)` с `total` без
    второго прохода.
    """

    service: str
    items: list[ServiceEventDetail]
    total: int
    has_more: bool = False
    limit: int
    offset: int
