"""Pydantic-схемы запроса/ответа для эндпоинтов /vms и VM-callback'ов воркера."""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.constants import (
    VM_STATUS_FREE,
    VmBusyState,
    VmCredStrategy,
    VmNetworkMode,
    VmPowerState,
)


class VmCreate(BaseModel):
    """Тело POST /vms — создать ВМ на hub-сервере (202 → dispatch VM_CREATE)."""

    hub_server_id: str = Field(description="Сервер-hub, на котором создаётся ВМ (prefix srv_). Обязан быть подготовлен как VMS-hub.")
    name: str = Field(..., min_length=1, max_length=255, description="Голое имя ВМ (без префикса stand<N>_). Уникально в пределах hub'а.")
    number: int | None = Field(default=None, ge=0, description="Опциональный номер стенда. Глобально уникален в паре servers+vm.")
    department_id: str = Field(description="Department-владелец ВМ. Должен совпадать с department'ом caller'а, иначе 403 DEPARTMENT_ISOLATION.")
    os_version: str | None = Field(default=None, max_length=64, description="Версия ОС ВМ (свободная строка, напр. 1.8.1.6). У universal-бокса опускается.")
    box: str | None = Field(default=None, max_length=128, description="Имя бокса-образа из FTP-каталога (vm_station / single-бокс).")
    network_mode: VmNetworkMode = Field(default=VmNetworkMode.BRIDGE, description="bridge (static в LAN) или nat (libvirt).")
    ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="Статический IP для bridge-режима (из пула). Опционален.")
    cpu: int = Field(..., ge=1, description="vCPU ВМ. Учитывается в проверке ёмкости hub'а.")
    ram_mb: int = Field(..., ge=1, description="RAM ВМ в МБ. Учитывается в проверке ёмкости hub'а.")
    disk_gb: int = Field(..., ge=1, description="Диск ВМ в ГБ. Учитывается в проверке ёмкости hub'а.")
    autostart: bool = Field(default=False, description="Автозапуск ВМ при старте hub'а.")
    cred_strategy: VmCredStrategy = Field(default=VmCredStrategy.PER_SNAPSHOT, description="Связь mgmt-кред со снимками: per_snapshot (дефолт) или reroll.")


class VmPowerRequest(BaseModel):
    """Тело POST /vms/{id}/power — питание ВМ (202 → dispatch VM_POWER)."""

    action: Literal["start", "shutdown", "reboot", "reset", "destroy"] = Field(
        ..., description="Действие питания: start/shutdown/reboot/reset/destroy.",
    )


class VmReserveRequest(BaseModel):
    """Тело POST /vms/{id}/reserve — бронь ВМ под тест.

    `status` опционален: не задан — бронь под свой логин (`identity.username`);
    задан — одно из `run test` / `debug test` / произвольная метка теста.
    Значение `free` тут запрещено (для снятия брони есть /release).
    """

    status: str | None = Field(
        default=None, max_length=64,
        description="Статус брони (run test / debug test / метка). Пусто — бронь под свой логин.",
    )

    @field_validator("status")
    @classmethod
    def _not_free(cls, value: str | None) -> str | None:
        if value is not None and value.strip() == VM_STATUS_FREE:
            raise ValueError("use POST /vms/{id}/release to free a VM, not reserve")
        if value is not None and not value.strip():
            raise ValueError("status must not be blank")
        return value


class VmStatusUpdate(BaseModel):
    """Тело PATCH /vms/{id}/status — прямое выставление брони (booking).

    Разрешены `free` / `run test` / `debug test` / `<login>`. Для симметрии
    со старым API оставлено отдельно от reserve/release.
    """

    status: str = Field(..., min_length=1, max_length=64, description="Новый booking-статус ВМ.")


class VmResponse(BaseModel):
    """Карточка ВМ в ответе GET/POST/list /vms."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="VM ID (prefix vm_).")
    name: str = Field(description="Голое имя ВМ.")
    number: int | None = Field(default=None, description="Номер стенда (или None).")
    hub_server_id: str = Field(description="Сервер-hub ВМ.")
    department_id: str = Field(description="Department-владелец.")
    os_version: str | None = Field(default=None, description="Версия ОС ВМ.")
    box: str | None = Field(default=None, description="Имя бокса-образа.")
    network_mode: str = Field(description="bridge / nat.")
    ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="IP ВМ (static/DHCP), None до назначения.")
    status: str = Field(description="Booking-статус: free / run test / debug test / <login>.")
    power_state: str = Field(description="Питание: on / off / unknown (из кэша).")
    power_state_checked_at: datetime | None = Field(default=None, description="Когда воркер последний раз пробовал питание ВМ (UTC).")
    cpu: int | None = Field(default=None, description="vCPU ВМ.")
    ram_mb: int | None = Field(default=None, description="RAM ВМ в МБ.")
    disk_gb: int | None = Field(default=None, description="Диск ВМ в ГБ.")
    autostart: bool = Field(default=False, description="Автозапуск при старте hub'а.")
    cred_strategy: str = Field(description="per_snapshot / reroll.")
    busy_state: str | None = Field(default=None, description="Lifecycle-lock (creating/deleting/updating/powering) или None.")
    busy_since: datetime | None = Field(default=None, description="Когда поставлен lifecycle-lock.")
    ping_reachable: bool | None = Field(default=None, description="Отвечает ли гость на ping (None — пробы не было).")
    ssh_reachable: bool | None = Field(default=None, description="Доступен ли SSH гостя (None — пробы не было).")
    last_error: str | None = Field(default=None, description="Последняя ошибка воркера по ВМ (или None).")
    created_at: datetime = Field(description="Когда карточка создана.")
    updated_at: datetime = Field(description="Когда карточка изменена в последний раз.")
    created_by: str | None = Field(default=None, description="user_id, создавший ВМ.")

    @classmethod
    def from_vm(cls, vm) -> "VmResponse":
        return cls.model_validate(vm)


class VmTaskDispatchResponse(BaseModel):
    """Стандартный ответ на dispatch VM-task'и (`{vm_id, task_id, status}`)."""

    vm_id: str = Field(description="ID ВМ (prefix vm_).")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(description="Статус: queued.")


class VmsHubPrepareResponse(BaseModel):
    """Ответ на dispatch VMS_HUB_PREPARE — task_id подготовки hub'а."""

    server_id: str = Field(description="Сервер, который готовим как VMS-hub.")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(description="Статус: queued.")


# ── Internal callbacks (worker → server_service) ────────────────────────────


class VmStateCallbackRequest(BaseModel):
    """Тело POST /internal/vms/{id}/state — воркер пишет состояние ВМ.

    Все поля опциональны — воркер шлёт то, что реально изменилось (идемпотентно,
    частичное обновление). `busy_state=null` снимает lifecycle-lock (терминал
    операции). `error` — текст ошибки последней операции (пишется в last_error).
    """

    power_state: VmPowerState | None = Field(default=None, description="Новое состояние питания ВМ (on/off/unknown).")
    ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="Назначенный/обнаруженный IP ВМ.")
    status: str | None = Field(default=None, max_length=64, description="Booking-статус (редко — воркер обычно его не трогает).")
    busy_state: VmBusyState | None = Field(default=None, description="Lifecycle-lock: непустой — операция идёт, null — снять лок.")
    clear_busy_state: bool = Field(default=False, description="Явно снять lifecycle-lock (busy_state → NULL). Нужен, т.к. null в busy_state неотличим от «не прислано».")
    ping_reachable: bool | None = Field(default=None, description="Результат ping-пробы гостя.")
    ssh_reachable: bool | None = Field(default=None, description="Результат SSH-пробы гостя.")
    error: str | None = Field(default=None, max_length=1024, description="Текст ошибки последней операции (в last_error).")


class VmStateCallbackResponse(BaseModel):
    """Подтверждение записи VM-state callback'а."""

    ok: bool = True
    vm_id: str = Field(description="ID ВМ.")
    power_state: str = Field(description="Итоговое состояние питания.")
    busy_state: str | None = Field(default=None, description="Итоговый lifecycle-lock (или None).")


class VmsHubStateCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{id}/vms-hub-state — воркер об исходе prepare-hub.

    `prepared=True` — hub готов: server_service ставит `is_vms_hub=True`,
    `vms_hub_prepared_at`, `virtualization=True`. `prepared=False` — упало:
    ставим `virtualization=False` (или оставляем), пишем ошибку. `phy_if` —
    детектнутый физ-интерфейс для моста (в network_interface_name).
    """

    prepared: bool = Field(description="True — hub подготовлен; False — prepare упал.")
    phy_if: str | None = Field(default=None, max_length=64, description="Детектнутый физ-интерфейс для моста (пишется в network_interface_name).")
    error: str | None = Field(default=None, max_length=1024, description="Текст ошибки (для prepared=False).")


class VmsHubStateCallbackResponse(BaseModel):
    """Подтверждение записи vms-hub-state callback'а."""

    ok: bool = True
    server_id: str = Field(description="Сервер-hub.")
    is_vms_hub: bool = Field(description="Текущее значение флага VMS-hub.")
