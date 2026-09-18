"""Схемы легаси-совместимых read-only маршрутов (`api/v1/endpoints/legacy_compat.py`, §12)."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
