"""Схемы для `/host/services` (статус) и `/host/services/{unit}/{action}` (control).

Две независимые категории, объединённые в один ответ:

* `astra` — внешние HTTP-сервисы (Jira/Life/Git/Releases) + DNS-reachability,
  проверяются живьём (`services/astra_health.py`).
* `allta` — systemd-юниты ALLTA на том же хосте, что и сам emm, статус через
  SSH (`services/host_control.py`); `not_configured`, если SSH ещё не настроен
  (`/settings/host-services`).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AstraHealthStatus = Literal["up", "down", "unknown"]
AlltaHealthStatus = Literal["up", "down", "unknown", "not_configured"]


class AstraServiceStatus(BaseModel):
    """Статус одного внешнего ASTRA-сервиса (HTTP или агрегированный DNS)."""

    id: str = Field(description="Машинный id: jira / life / git / releases / dns.")
    label: str = Field(description="Человекочитаемая метка.")
    status: AstraHealthStatus = Field(description="up / down / unknown (timeout — реальное состояние неизвестно).")
    checked_at: datetime = Field(description="Момент проверки, UTC.")
    latency_ms: int | None = Field(default=None, description="Время ответа, мс. null при ошибке/таймауте.")
    error: str | None = Field(default=None, description="Причина не-up статуса. null при успехе.")


class AlltaServiceStatus(BaseModel):
    """Статус одного ALLTA systemd-юнита на хосте."""

    id: str = Field(description="Машинный id юнита (см. `ALLTA_HOST_UNITS`), напр. `acs`.")
    label: str = Field(description="Человекочитаемая метка.")
    status: AlltaHealthStatus = Field(
        description="up / down (active/inactive по systemctl is-active) / unknown (SSH недоступен) / not_configured (SSH не настроен)."
    )
    checked_at: datetime = Field(description="Момент проверки, UTC.")
    error: str | None = Field(default=None, description="Причина unknown-статуса (SSH-ошибка). null иначе.")


class HostServicesStatusResponse(BaseModel):
    """Ответ `GET /host/services` — обе категории разом."""

    astra: list[AstraServiceStatus] = Field(description="4 внешних HTTP-сервиса + агрегированная строка DNS.")
    allta: list[AlltaServiceStatus] = Field(description="12 systemd-юнитов ALLTA на хосте.")


class HostServiceControlResult(BaseModel):
    """Ответ успешного `POST /host/services/{unit}/{action}`."""

    ok: bool = Field(description="Всегда true при 200 — ошибки идут через error envelope.")
    unit: str = Field(description="Машинный id юнита.")
    action: Literal["start", "stop", "restart"] = Field(description="Выполненное действие.")
    output: str = Field(description="stdout guard-скрипта/systemctl (обычно пусто на успехе).")
