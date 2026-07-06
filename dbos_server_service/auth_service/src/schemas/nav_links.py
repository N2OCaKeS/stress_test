"""Схемы настраиваемой кнопки левой панели web-UI (`/nav-links`)."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_MAX_URL_LEN = 2048


def _validate_http_url(v: str) -> str:
    """Отбить всё, кроме http(s) абсолютных URL разумной длины."""
    v = v.strip()
    if not v:
        raise ValueError("url не может быть пустым")
    if len(v) > _MAX_URL_LEN:
        raise ValueError(f"url длиннее {_MAX_URL_LEN} символов")
    low = v.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise ValueError("url должен начинаться с http:// или https://")
    return v


class NavLinkItem(BaseModel):
    """Одна видимая кнопка в ответе `GET /nav-links`."""
    label: str
    url: str


class NavLinkConfig(BaseModel):
    """Полная конфигурация кнопки (ответ `GET /admin/nav-links`)."""
    enabled: bool
    label: str
    url: str | None = None
    all_departments: bool
    department_ids: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None
    updated_by: str | None = None


class NavLinkConfigUpdate(BaseModel):
    """Тело `PUT /admin/nav-links` — полная замена конфигурации."""
    enabled: bool = False
    label: str = Field(default="allta", min_length=1, max_length=64)
    url: str | None = Field(default=None, max_length=_MAX_URL_LEN)
    all_departments: bool = False
    department_ids: list[str] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("label не может быть пустым")
        return v

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        return _validate_http_url(v)
