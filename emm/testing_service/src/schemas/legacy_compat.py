"""Схемы легаси-совместимых маршрутов.

* `api/v1/endpoints/legacy_compat.py` (§12) — read-only справочники под
  авторизацией и настройки публичного compat;
* `api/legacy_public.py` — публичные `/rest/api/*` отвечают в
  легаси-формате без этих схем (голый текст / JSON как в `allta_front.py`).
"""

from __future__ import annotations

import ipaddress
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LegacyUrlResponse(BaseModel):
    """Ответ `get-jira-url`/`get-confluence-url`.

    Легаси отдавал голую строку без обёртки (`allta_front.py:911-918`) —
    здесь JSON-объект, потому что значение теперь per-department и может
    отсутствовать (интеграция ещё не настроена).
    """

    department_id: str
    url: str | None = Field(default=None, description="Пусто, если интеграция отдела не настроена.")


class AvailableKernelsResponse(BaseModel):
    """Ответ `available-kernels-from-<rc>`. Легаси отдавал голый JSON-массив строк."""

    rc: str
    kernels: list[str]


# ── Настройки публичного compat `/rest/api/*` ────────────────────────


def normalize_cidr(value: str) -> str:
    """Подсеть в каноническом виде; адрес с ненулевыми битами хоста — ошибка.

    `10.177.103.5/24` не превращается молча в `10.177.103.0/24`: это почти
    всегда опечатка, и администратор должен увидеть, какую сеть он открывает.
    Одиночный адрес без маски — `/32` (`/128`).
    """
    text = value.strip()
    try:
        return str(ipaddress.ip_network(text, strict=True))
    except ValueError as exc:
        try:
            suggestion = str(ipaddress.ip_network(text, strict=False))
        except ValueError:
            raise ValueError(f"not an IP network: {text!r} (e.g. 10.177.103.0/24)") from exc
        raise ValueError(f"host bits are set in {text!r}; did you mean {suggestion}?") from exc


def _clean_description(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class CompatNetworkResponse(BaseModel):
    """Одна разрешённая подсеть."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    cidr: str
    description: str | None = None
    enabled: bool
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CompatNetworksResponse(BaseModel):
    items: list[CompatNetworkResponse]


class CompatNetworkCreate(BaseModel):
    """Тело `POST /legacy-compat/networks`."""

    cidr: str = Field(..., min_length=1, max_length=64, description="Подсеть (`10.177.103.0/24`) или адрес.")
    description: str | None = Field(default=None, max_length=256)
    enabled: bool = True

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, value: str) -> str:
        return normalize_cidr(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        return _clean_description(value)


class CompatNetworkUpdate(BaseModel):
    """Тело `PATCH /legacy-compat/networks/{id}`. Явный `null` у `description` очищает поле."""

    cidr: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=256)
    enabled: bool | None = None

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, value: str | None) -> str | None:
        return None if value is None else normalize_cidr(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        return _clean_description(value)


class LegacyCompatSettingsResponse(BaseModel):
    default_department_id: str | None = Field(
        default=None,
        description="Отдел для URL интеграций, если IP источника не принадлежит стенду.",
    )
    updated_by: str | None = None
    updated_at: datetime | None = None


class LegacyCompatSettingsUpdate(BaseModel):
    """Тело `PUT /legacy-compat/settings`. `null` или пустая строка — отдел не выбран."""

    default_department_id: str | None = Field(default=None, max_length=64)

    @field_validator("default_department_id")
    @classmethod
    def _dept(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class CompatResolveResponse(BaseModel):
    """Ответ `GET /legacy-compat/resolve?ip=` — что compat сделает с запросом с этого адреса."""

    ip: str
    allowed: bool = Field(description="Адрес попадает во включённую подсеть.")
    network_id: str | None = None
    cidr: str | None = None
    department_id: str | None = Field(default=None, description="Отдел для URL интеграций.")
    reason: str = Field(
        description=(
            "`stand` — найден стенд; `default` — стенда с этим IP нет, взят отдел по "
            "умолчанию; `ambiguous` — стенды с этим IP в разных отделах, взят отдел по "
            "умолчанию; `no_default` — отдел не определён (стенда нет, отдел по "
            "умолчанию не выбран)."
        ),
    )
    stand_ids: list[str] = Field(default_factory=list, description="Стенды с этим IP.")
