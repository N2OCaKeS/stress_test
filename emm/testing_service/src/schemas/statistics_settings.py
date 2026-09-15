"""Pydantic-схемы `/statistics/settings` (§2.7, §9.3 плана миграции)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StatisticsSettingsResponse(BaseModel):
    """Текущие настройки. Публичные — секретов здесь нет (base_url не секрет)."""

    enabled: bool
    base_url: str | None = None


class StatisticsSettingsUpdate(BaseModel):
    """Частичное обновление: непереданное поле сохраняет текущее значение.

    Пустая строка в `base_url` — явная очистка (тот же приём, что
    `AcsSettingsUpdate.acs_url` в server_service).
    """

    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=256)
