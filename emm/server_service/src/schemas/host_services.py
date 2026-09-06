"""Схемы для `/host/services` (статус) и `/host/services/{unit_id}/{action}` (control).

Две независимые категории, объединённые в один ответ:

* `astra` — внешние HTTP-сервисы (Jira/Life/Git/Releases) + DNS-reachability,
  проверяются живьём (`services/astra_health.py`), платформенные, не меняется.
* `allta` — systemd-юниты, которые КАЖДЫЙ отдел сам добавил себе в список
  (`HostServiceUnit`), статус через SSH на СВОЙ хост
  (`services/host_control.py`); пусто (`[]`), если у отдела ещё нет ни
  SSH-конфига, ни единого юнита в списке — это не ошибка.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AstraHealthStatus = Literal["up", "down", "unknown"]
AlltaHealthStatus = Literal["up", "down", "unknown"]


class AstraServiceStatus(BaseModel):
    """Статус одного внешнего ASTRA-сервиса (HTTP или агрегированный DNS)."""

    id: str = Field(description="Машинный id: jira / life / git / releases / dns.")
    label: str = Field(description="Человекочитаемая метка.")
    status: AstraHealthStatus = Field(description="up / down / unknown (timeout — реальное состояние неизвестно).")
    checked_at: datetime = Field(description="Момент проверки, UTC.")
    latency_ms: int | None = Field(default=None, description="Время ответа, мс. null при ошибке/таймауте.")
    error: str | None = Field(default=None, description="Причина не-up статуса. null при успехе.")


class AlltaServiceStatus(BaseModel):
    """Статус одного systemd-юнита из списка отдела."""

    id: str = Field(description="`hsu_<uuid>` — id строки `HostServiceUnit`, используется и как path-параметр control.")
    label: str = Field(description="Display-имя юнита (см. `HostServiceUnit.label`).")
    status: AlltaHealthStatus = Field(
        description="up / down (active/inactive по systemctl is-active) / unknown (SSH недоступен)."
    )
    checked_at: datetime = Field(description="Момент проверки, UTC.")
    error: str | None = Field(default=None, description="Причина unknown-статуса (SSH-ошибка). null иначе.")


class HostServicesStatusResponse(BaseModel):
    """Ответ `GET /host/services` — обе категории разом."""

    astra: list[AstraServiceStatus] = Field(description="4 внешних HTTP-сервиса + агрегированная строка DNS.")
    allta: list[AlltaServiceStatus] = Field(
        description="Юниты своего отдела. Пусто для платформенных ролей (нет department_id) и для отдела без конфига/юнитов."
    )


class HostServiceControlResult(BaseModel):
    """Ответ успешного `POST /host/services/{unit_id}/{action}`."""

    ok: bool = Field(description="Всегда true при 200 — ошибки идут через error envelope.")
    unit_id: str = Field(description="`hsu_<uuid>` юнита, который был затронут.")
    action: Literal["start", "stop", "restart"] = Field(description="Выполненное действие.")
    output: str = Field(description="stdout guard-скрипта/systemctl (обычно пусто на успехе).")
