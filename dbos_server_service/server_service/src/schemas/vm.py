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
    VmSnapshotType,
)


class VmCreate(BaseModel):
    """Тело POST /vms — создать ВМ на hub-сервере (202 → dispatch VM_CREATE)."""

    hub_server_id: str = Field(description="Сервер-hub, на котором создаётся ВМ (prefix srv_). Обязан быть подготовлен как VMS-hub.")
    name: str = Field(..., min_length=1, max_length=255, description="Голое имя ВМ (без префикса stand<N>_). Уникально в пределах hub'а.")
    hostname: str | None = Field(default=None, max_length=255, description="Hostname гостя. Опционален: пусто → имя ВМ (name).")
    accounts: list[str] = Field(default_factory=list, description="ID существующих server_account отдела для провижна в госте (привязка к ВМ). Аккаунты должны быть того же отдела и доступны caller'у.")
    number: int | None = Field(default=None, ge=0, description="Опциональный номер стенда. Глобально уникален в паре servers+vm.")
    department_id: str = Field(description="Department-владелец ВМ. Должен совпадать с department'ом caller'а, иначе 403 DEPARTMENT_ISOLATION.")
    os_version: str | None = Field(default=None, max_length=64, description="Версия ОС ВМ (свободная строка, напр. 1.8.1.6). У universal-бокса опускается.")
    box: str | None = Field(default=None, max_length=128, description="Имя бокса-образа из FTP-каталога (vm_station / single-бокс).")
    network_mode: VmNetworkMode = Field(default=VmNetworkMode.BRIDGE, description="bridge (static в LAN) или nat (libvirt).")
    ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="Статический IP для bridge-режима (проверяется на занятость). Опционален — если не задан, берётся из пула.")
    pool_id: str | None = Field(default=None, description="Пул для авто-выбора свободного IP (bridge, если ip_address не задан).")
    cpu: int = Field(..., ge=1, description="vCPU ВМ. Учитывается в проверке ёмкости hub'а.")
    ram_mb: int = Field(..., ge=1, description="RAM ВМ в МБ. Учитывается в проверке ёмкости hub'а.")
    disk_gb: int = Field(..., ge=1, description="Диск ВМ в ГБ. Учитывается в проверке ёмкости hub'а.")
    autostart: bool = Field(default=False, description="Автозапуск ВМ при старте hub'а.")
    cred_strategy: VmCredStrategy = Field(default=VmCredStrategy.PER_SNAPSHOT, description="Связь mgmt-кред со снимками: per_snapshot (дефолт) или reroll.")


class VmUpdateRequest(BaseModel):
    """Тело PATCH /vms/{id} — изменить ресурсы ВМ (202 → dispatch VM_UPDATE).

    Меняются только cpu / ram_mb (stop→правка XML→start на воркере). Оба поля
    опциональны; пустое тело → 422 (нечего менять). Увеличение проверяется на
    ёмкость hub'а.
    """

    cpu: int | None = Field(default=None, ge=1, description="Новое число vCPU ВМ.")
    ram_mb: int | None = Field(default=None, ge=1, description="Новый объём RAM ВМ в МБ.")


class VmDiskCreate(BaseModel):
    """Тело POST /vms/{id}/disks — создать+подключить диск (202 → VM_DISK_ATTACH)."""

    name: str = Field(..., min_length=1, max_length=255, description="Имя диска (уникально в пределах ВМ, входит в serial).")
    size_gb: int = Field(..., ge=1, description="Размер диска в ГБ.")
    fs: str | None = Field(default=None, max_length=32, description="ФС для форматирования (ext4/xfs/...), опционально.")
    mount: str | None = Field(default=None, max_length=255, description="Точка монтирования в госте, опционально.")
    target_dev: str | None = Field(default=None, max_length=16, description="Желаемое имя устройства (vdb/vdc); по умолчанию воркер назначает следующее свободное.")


class VmDiskResizeRequest(BaseModel):
    """Тело POST /vms/{id}/disks/{disk_id}/resize (202 → VM_DISK_RESIZE)."""

    size_gb: int = Field(..., ge=1, description="Новый размер диска в ГБ (только увеличение).")


class VmPowerRequest(BaseModel):
    """Тело POST /vms/{id}/power — питание ВМ (202 → dispatch VM_POWER)."""

    action: Literal["start", "shutdown", "reboot", "reset", "destroy"] = Field(
        ..., description="Действие питания: start/shutdown/reboot/reset/destroy.",
    )


class VmAutostartRequest(BaseModel):
    """Тело POST /vms/{id}/autostart — включить/выключить автозапуск (202 → VM_SET_AUTOSTART)."""

    enabled: bool = Field(..., description="True — ВМ стартует при старте hub'а (`virsh autostart`); False — выключить.")


class VmConsoleRequest(BaseModel):
    """Тело POST /vms/{id}/console — запросить доступ к консоли ВМ."""

    kind: Literal["ssh", "vnc", "serial"] = Field(
        default="vnc",
        description="Тип консоли: ssh (интерактивный shell), vnc (websockify+noVNC), serial (`virsh console`).",
    )


class VmConsoleResponse(BaseModel):
    """Ответ на POST /vms/{id}/console — контракт подключения UI к консоли ВМ.

    Реальный проброс держит отдельный websockify/PTY-прокси (ставится позже):
    UI подключается к нему по `host`+`ws_path`, предъявляя `token` (живёт
    `expires_in` секунд). Прокси валидирует токен и резолвит фактический
    VNC-дисплей/serial-устройство ВМ на hub'е. server_service токен не хранит.
    """

    vm_id: str = Field(description="ID ВМ (prefix vm_).")
    kind: str = Field(description="ssh / vnc / serial.")
    token: str = Field(description="Короткоживущий токен доступа (prefix vmc_); предъявляется прокси.")
    expires_in: int = Field(description="Сколько секунд токен действителен.")
    host: str | None = Field(default=None, description="Хост, к которому подключается UI: для vnc/serial — IP hub'а (там живёт прокси), для ssh — IP гостя (None, если гость ещё без IP).")
    ws_path: str = Field(description="Путь websocket-эндпоинта прокси для этой ВМ и типа консоли.")
    port: int | None = Field(default=None, description="VNC-порт (для kind=vnc, если известен) либо SSH-порт (для kind=ssh). Для serial — None.")
    serial_path: str | None = Field(default=None, description="Устройство serial-консоли в госте (для kind=serial), иначе None.")
    username: str | None = Field(default=None, description="Управляющий пользователь для kind=ssh (mgmt_user или дефолт-учётка образа). Пароль/ключ прокси тянет через internal mgmt-credentials — plaintext в ответе не отдаётся.")


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
    hostname: str | None = Field(default=None, description="Hostname гостя (или None → имя ВМ).")
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


class VmBulkCreateRequest(BaseModel):
    """Тело POST /vms/bulk — создать несколько разных ВМ за один запрос.

    Каждый элемент — самостоятельный `VmCreate` (свой hub/имя/ресурсы/сеть/
    учётки). Диспатчим по задаче `vm.create` на элемент; одна упавшая не валит
    остальные (per-item результат). Ёмкость hub'а копится по мере создания.
    """

    items: list[VmCreate] = Field(
        ..., min_length=1,
        description="ВМ для создания, по одному элементу на ВМ.",
    )


class VmBulkCreateResult(BaseModel):
    """Per-item исход массового создания ВМ.

    `status=created` — ВМ заведена, `vm_id`/`task_id` заполнены. `status=error`
    — элемент упал, `error_code`/`message` несут причину (дубль имени, чужой
    отдел, ёмкость hub'а, битый бокс и т.д.); остальные элементы продолжают.
    """

    index: int = Field(description="Позиция элемента в исходном списке items.")
    name: str = Field(description="Имя ВМ из запроса.")
    status: str = Field(description="created | error.")
    vm_id: str | None = Field(default=None, description="ID созданной ВМ (prefix vm_), если created.")
    task_id: str | None = Field(default=None, description="ID задачи vm.create (prefix tsk_), если created.")
    error_code: str | None = Field(default=None, description="Стабильный error_code (для status=error).")
    message: str | None = Field(default=None, description="Человекочитаемое описание ошибки (для status=error).")


class VmBulkCreateResponse(BaseModel):
    """Ответ POST /vms/bulk — per-item результаты + сводные счётчики."""

    results: list[VmBulkCreateResult] = Field(description="Исход по каждому элементу (в порядке items).")
    created_count: int = Field(description="Сколько ВМ создано.")
    error_count: int = Field(description="Сколько элементов упало.")


class VmDiskResponse(BaseModel):
    """Карточка диска ВМ в ответе GET/list."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Disk ID (prefix vmd_).")
    vm_id: str = Field(description="ID ВМ-владельца.")
    name: str = Field(description="Имя диска.")
    size_gb: int = Field(description="Размер диска в ГБ.")
    path: str | None = Field(default=None, description="Путь к qcow2 в пуле hub'а (None до создания).")
    target_dev: str | None = Field(default=None, description="Устройство в госте (vdb/...), None до attach'а.")
    serial: str | None = Field(default=None, description="Serial устройства (<vm>_<disk>).")
    is_system: bool = Field(default=False, description="Системный диск ВМ (root).")
    fs: str | None = Field(default=None, description="Файловая система.")
    mount: str | None = Field(default=None, description="Точка монтирования.")
    state: str = Field(description="creating / ready / error.")
    created_at: datetime = Field(description="Когда строка диска заведена.")


class VmDiskDispatchResponse(BaseModel):
    """Ответ на dispatch VM_DISK_* — vm_id + disk_id + task_id."""

    vm_id: str = Field(description="ID ВМ (prefix vm_).")
    disk_id: str = Field(description="ID диска (prefix vmd_).")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(description="Статус: queued.")


class VmImageResponse(BaseModel):
    """Карточка бокса-образа из каталога vm_images."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Image ID (prefix vmi_).")
    name: str = Field(description="Имя бокса (vm_station / single-бокс).")
    url: str = Field(description="URL артефакта .tar.gz.")
    kind: str = Field(description="universal / single.")
    hub_server_id: str | None = Field(default=None, description="Привязка к hub'у (None — глобальный образ).")
    os_versions: list[str] = Field(default_factory=list, description="ОС внутри universal-бокса (для single — пусто).")
    min_disk_gb: int | None = Field(default=None, description="Минимальный размер системного диска (ГБ); None — данных нет. UI предупреждает, если запрошенный диск меньше.")
    created_at: datetime = Field(description="Когда запись добавлена.")
    updated_at: datetime = Field(description="Когда запись изменена.")


class VmImageRefreshResponse(BaseModel):
    """Ответ на POST /vm-images/refresh — итог синка с FTP-конфига."""

    ok: bool = True
    synced: int = Field(description="Сколько записей обработано (created + updated).")
    created: int = Field(description="Сколько образов добавлено.")
    updated: int = Field(description="Сколько образов обновлено.")
    source: str = Field(description="URL конфига боксов, с которого синкали.")


class VmsHubPrepareResponse(BaseModel):
    """Ответ на dispatch VMS_HUB_PREPARE — task_id подготовки hub'а."""

    server_id: str = Field(description="Сервер, который готовим как VMS-hub.")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(description="Статус: queued.")


# ── снимки ВМ ────────────────────────────────────────────────────────────────


class VmSnapshotCreate(BaseModel):
    """Тело POST /vms/{id}/snapshots — снять снимок (202 → VM_SNAPSHOT_CREATE)."""

    name: str = Field(..., min_length=1, max_length=255, description="Имя снимка (уникально в пределах ВМ). Суффикс _build зарезервирован под системные снимки.")
    description: str | None = Field(default=None, max_length=1024, description="Описание снимка (опционально).")
    snapshot_type: VmSnapshotType = Field(default=VmSnapshotType.DISK_ONLY, description="disk_only (только диск) или full (диск + RAM/устройства).")


class VmSnapshotResponse(BaseModel):
    """Карточка снимка ВМ в ответе GET/list. Системные `<ver>_build` в выдаче скрыты."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Snapshot ID (prefix snp_).")
    vm_id: str = Field(description="ID ВМ-владельца.")
    name: str = Field(description="Имя снимка.")
    description: str | None = Field(default=None, description="Описание снимка.")
    parent_snapshot_id: str | None = Field(default=None, description="Родительский снимок в цепочке (или None).")
    snapshot_type: str = Field(description="Способ снятия: disk_only / full.")
    kind: str = Field(description="Смысловая группа: os_baseline / user.")
    os_version: str | None = Field(default=None, description="Версия ОС снимка (или None).")
    mode: str | None = Field(default=None, description="Режим Astra снимка: oryol / smolensk / None.")
    is_system: bool = Field(default=False, description="Системный golden-снимок (`<ver>_build`). В штатной выдаче не появляется.")
    state: str = Field(description="creating / ready / error.")
    size_bytes: int | None = Field(default=None, description="Размер снимка в байтах (None до синка с hub'а).")
    is_current: bool = Field(default=False, description="Текущий снимок ВМ (на него указывает активное состояние диска).")
    created_at: datetime = Field(description="Когда строка снимка заведена.")
    created_by: str | None = Field(default=None, description="user_id, снявший снимок.")


class VmSnapshotDispatchResponse(BaseModel):
    """Ответ на dispatch VM_SNAPSHOT_* — vm_id + snapshot_id + task_id."""

    vm_id: str = Field(description="ID ВМ (prefix vm_).")
    snapshot_id: str | None = Field(default=None, description="ID снимка (prefix snp_); None для revert/delete по имени существующего.")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(description="Статус: queued.")


class VmCredStrategyRequest(BaseModel):
    """Тело PATCH /vms/{id}/cred-strategy — режим mgmt-кред ВМ (синхронно)."""

    cred_strategy: VmCredStrategy = Field(..., description="per_snapshot / reroll.")


class VmAstraUpdateRequest(BaseModel):
    """Тело POST /vms/{id}/astra-update — обновить ОС ВМ по RC (202 → VM_ASTRA_UPDATE)."""

    rc: str = Field(..., min_length=1, max_length=64, description="Целевой RC (build-версия, напр. 1.7.5.6). Снимок с этим именем не должен существовать (409).")
    password: str | None = Field(default=None, description="Новый пароль гостевого `u` после обновления (опционально).")
    cred_strategy: VmCredStrategy | None = Field(default=None, description="Override стратегии кред на эту операцию (по умолчанию — cred_strategy ВМ).")


class VmAlltaUpdateRequest(BaseModel):
    """Тело POST /vms/{id}/allta-update — обновить гостевую allta + опц. пароль."""

    password: str | None = Field(default=None, description="Новый пароль гостевого `u` (опционально для allta-update).")
    cred_strategy: VmCredStrategy | None = Field(default=None, description="Override стратегии кред на эту операцию (по умолчанию — cred_strategy ВМ).")


class VmPasswdRequest(BaseModel):
    """Тело POST /vms/{id}/passwd — сменить пароль гостевого `u` (тот же op, пароль обязателен)."""

    password: str = Field(..., min_length=1, description="Новый пароль гостевого `u`. Обязателен.")
    cred_strategy: VmCredStrategy | None = Field(default=None, description="Override стратегии кред на эту операцию (по умолчанию — cred_strategy ВМ).")


# ── prepare / mgmt-creds / сеть ──────────────────────────────────────────────


class VmNetworkRequest(BaseModel):
    """Тело POST /vms/{id}/network — сменить сетевой режим ВМ (202 → VM_SET_NETWORK).

    `network_mode` обязателен (bridge/nat). Для bridge можно задать конкретный
    `ip_address` (проверяется на занятость) либо `pool_id` (адрес выбирается
    аллокатором из пула). Для nat адрес выдаёт libvirt (domifaddr) — оба поля
    опущены.
    """

    network_mode: VmNetworkMode = Field(..., description="bridge (static из пула) или nat (libvirt).")
    ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="Конкретный статический IP для bridge (проверяется на занятость). Опционален.")
    pool_id: str | None = Field(default=None, description="Пул для авто-выбора свободного IP (bridge, если ip_address не задан).")


class VmMgmtCredentialsResponse(BaseModel):
    """Ответ GET /internal/vms/{id}/mgmt-credentials — per-VM управляющие креды worker'у."""

    management_user: str | None = Field(description="Имя управляющего пользователя ВМ. None — ВМ ещё не prepared.")
    ssh_private_key: str = Field(description="Расшифрованный приватный SSH-ключ управляющего пользователя (PEM, только worker'у).")
    password: str = Field(description="Расшифрованный пароль управляющего пользователя (plaintext, только worker'у).")


class VmPreparedCallbackRequest(BaseModel):
    """Тело POST /internal/vms/{id}/prepared — worker подтверждает онбординг ВМ.

    После установки per-VM управляющих кред в госте (vm.prepare/ротация) воркер
    зовёт этот callback. `management_user` — имя заведённого пользователя.
    Опциональные креды (`mgmt_*` plaintext) дают воркеру перезаписать реально
    установленный материал — server_service шифрует их под AAD ВМ; если не
    присланы, остаётся ciphertext, записанный при dispatch'е.
    """

    prepared: bool = Field(default=True, description="True — управляющие креды установлены в госте; False — prepare упал.")
    management_user: str | None = Field(default=None, max_length=64, description="Имя управляющего пользователя ВМ (заведён в госте).")
    mgmt_password: str | None = Field(default=None, description="Реально установленный пароль (plaintext; server_service шифрует). Опционально.")
    mgmt_ssh_public_key: str | None = Field(default=None, description="Публичный ключ управляющего пользователя (plaintext). Опционально.")
    mgmt_ssh_private_key: str | None = Field(default=None, description="Приватный ключ управляющего пользователя (plaintext; server_service шифрует). Опционально.")
    error: str | None = Field(default=None, max_length=1024, description="Текст ошибки (для prepared=False).")


class VmPreparedCallbackResponse(BaseModel):
    """Подтверждение записи prepared-callback'а ВМ."""

    ok: bool = True
    vm_id: str = Field(description="ID ВМ.")
    is_managed: bool = Field(description="Текущее значение флага управляемости ВМ.")


# ── IPAM: пулы IP-адресов ВМ ─────────────────────────────────────────────────


class VmIpPoolCreate(BaseModel):
    """Тело POST /vm-ip-pools — создать пул IP-адресов bridge-ВМ."""

    name: str = Field(..., min_length=1, max_length=255, description="Имя пула (уникально в пределах отдела).")
    department_id: str = Field(..., description="Department-владелец пула. Должен совпадать с department'ом caller'а.")
    cidr: str = Field(..., min_length=1, max_length=64, description="Подсеть пула (10.177.103.0/24).")
    gateway: IPv4Address | IPv6Address | None = Field(default=None, description="Шлюз по умолчанию для гостя.")
    netmask: str | None = Field(default=None, max_length=64, description="Маска подсети (255.255.255.0), опционально.")
    dns: list[str] = Field(default_factory=list, description="DNS-серверы для провижна статики в госте.")
    range_start: IPv4Address | IPv6Address = Field(..., description="Первый адрес диапазона выдачи (включительно).")
    range_end: IPv4Address | IPv6Address = Field(..., description="Последний адрес диапазона выдачи (включительно).")
    server_id: str | None = Field(default=None, description="Override на конкретный hub-сервер (None — пул отдельский).")


class VmIpPoolUpdate(BaseModel):
    """Тело PATCH /vm-ip-pools/{id} — частичное изменение пула (только присланные поля)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    cidr: str | None = Field(default=None, min_length=1, max_length=64)
    gateway: IPv4Address | IPv6Address | None = Field(default=None)
    netmask: str | None = Field(default=None, max_length=64)
    dns: list[str] | None = Field(default=None)
    range_start: IPv4Address | IPv6Address | None = Field(default=None)
    range_end: IPv4Address | IPv6Address | None = Field(default=None)
    server_id: str | None = Field(default=None)


class VmIpPoolResponse(BaseModel):
    """Карточка пула IP-адресов ВМ."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Pool ID (prefix pool_).")
    name: str = Field(description="Имя пула.")
    department_id: str = Field(description="Department-владелец.")
    cidr: str = Field(description="Подсеть пула.")
    gateway: IPv4Address | IPv6Address | None = Field(default=None, description="Шлюз.")
    netmask: str | None = Field(default=None, description="Маска подсети.")
    dns: list[str] = Field(default_factory=list, description="DNS-серверы.")
    range_start: IPv4Address | IPv6Address = Field(description="Первый адрес диапазона.")
    range_end: IPv4Address | IPv6Address = Field(description="Последний адрес диапазона.")
    server_id: str | None = Field(default=None, description="Override на hub-сервер (или None).")
    created_at: datetime = Field(description="Когда пул создан.")
    updated_at: datetime = Field(description="Когда пул изменён в последний раз.")


class VmAvailableIpsResponse(BaseModel):
    """Ответ GET /vms/available-ips — свободные адреса пула."""

    pool_id: str = Field(description="ID пула.")
    available: list[str] = Field(default_factory=list, description="Свободные IP из диапазона (за вычетом занятых).")
    total_free: int = Field(description="Сколько адресов свободно всего (может превышать длину available из-за cap'а).")


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


class VmDiskStateItem(BaseModel):
    """Один диск в callback'е синка дисков ВМ."""

    disk_id: str = Field(description="ID диска (prefix vmd_), которому принадлежат факты.")
    state: str | None = Field(default=None, max_length=16, description="Новое состояние диска: ready / error (или creating).")
    path: str | None = Field(default=None, max_length=512, description="Путь к созданному qcow2 в пуле.")
    target_dev: str | None = Field(default=None, max_length=16, description="Назначенное устройство в госте (vdb/...).")
    serial: str | None = Field(default=None, max_length=128, description="Serial подключённого устройства.")
    size_gb: int | None = Field(default=None, ge=1, description="Фактический размер после resize.")


class VmDisksCallbackRequest(BaseModel):
    """Тело POST /internal/vms/{id}/disks — воркер синкает факты дисков ВМ.

    Частичный, идемпотентный апдейт: применяем присланные поля к перечисленным
    дискам (по `disk_id`). Незнакомые disk_id пропускаются (диск мог быть удалён).
    """

    disks: list[VmDiskStateItem] = Field(default_factory=list, description="Список дисков с их актуальными фактами.")


class VmDisksCallbackResponse(BaseModel):
    """Подтверждение записи disk-sync callback'а."""

    ok: bool = True
    vm_id: str = Field(description="ID ВМ.")
    synced: int = Field(description="Сколько дисков фактически обновлено.")


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


class VmSnapshotSyncItem(BaseModel):
    """Один снимок в батч-синке `POST /internal/vms/{id}/snapshots`.

    Матчинг — по имени (`name`) в пределах ВМ: известный снимок обновляется,
    незнакомый заводится (worker создаёт `<ver>_build`/`<ver>` в ходе vm.create).
    Поля креды (`mgmt_user`/`mgmt_password`/`mgmt_ssh_private_key`) приходят
    plaintext'ом (у воркера нет ключа) — server_service шифрует их под AAD
    снимка (режим per_snapshot). `is_current=True` делает снимок текущим и
    снимает флаг с остальных (revert).
    """

    name: str = Field(..., min_length=1, max_length=255, description="Имя снимка (ключ матчинга в пределах ВМ).")
    parent: str | None = Field(default=None, max_length=255, description="Имя родительского снимка в цепочке (резолвится в parent_snapshot_id).")
    snapshot_type: str | None = Field(default=None, max_length=16, description="Способ снятия: disk_only / full.")
    kind: str | None = Field(default=None, max_length=16, description="Смысловая группа: os_baseline / user.")
    os_version: str | None = Field(default=None, max_length=64, description="Версия ОС снимка (`<ver>`).")
    mode: str | None = Field(default=None, max_length=16, description="Режим Astra снимка: oryol / smolensk.")
    is_system: bool | None = Field(default=None, description="Системный `<ver>_build` (скрыт, защищён от ручного delete/revert).")
    state: str | None = Field(default=None, max_length=16, description="creating / ready / error.")
    size_bytes: int | None = Field(default=None, ge=0, description="Размер снимка в байтах.")
    is_current: bool | None = Field(default=None, description="True — снимок стал текущим (revert); флаг снимается с остальных снимков ВМ.")
    mgmt_user: str | None = Field(default=None, max_length=64, description="Гостевой аккаунт кред снимка (обычно `u`).")
    mgmt_password: str | None = Field(default=None, description="Пароль кред снимка (plaintext; server_service шифрует).")
    mgmt_ssh_private_key: str | None = Field(default=None, description="Приватный SSH-ключ кред снимка (plaintext; server_service шифрует).")


class VmSnapshotsCallbackRequest(BaseModel):
    """Тело POST /internal/vms/{id}/snapshots — воркер синкает снимки ВМ по имени.

    Частичный, идемпотентный upsert по имени. Незнакомые снимки заводятся,
    известные обновляются. Снимки, не пришедшие в батче, не трогаются (воркер
    сам решает, слать полный список или дельту).
    """

    snapshots: list[VmSnapshotSyncItem] = Field(default_factory=list, description="Список снимков с актуальными фактами (и опц. кредами).")


class VmSnapshotsCallbackResponse(BaseModel):
    """Подтверждение записи snapshot-sync callback'а."""

    ok: bool = True
    vm_id: str = Field(description="ID ВМ.")
    synced: int = Field(description="Сколько снимков обработано (created + updated).")
    created: int = Field(description="Сколько снимков заведено.")
    updated: int = Field(description="Сколько снимков обновлено.")


# ── пресеты стандартных ВМ (vm_preset) + create-default-vms ─────────────────


class VmPresetCreate(BaseModel):
    """Тело POST /vm-presets — создать шаблон стандартной ВМ отдела."""

    name: str = Field(..., min_length=1, max_length=255, description="Имя пресета (уникально в пределах отдела). Становится именем развёрнутой ВМ.")
    department_id: str = Field(..., description="Department-владелец пресета. Должен совпадать с department'ом caller'а.")
    box: str | None = Field(default=None, max_length=128, description="Имя бокса-образа из FTP-каталога (vm_station / single-бокс).")
    os_version: str | None = Field(default=None, max_length=64, description="Версия ОС ВМ (свободная строка). У universal-бокса опускается.")
    cpu: int = Field(..., ge=1, description="vCPU ВМ.")
    ram_mb: int = Field(..., ge=1, description="RAM ВМ в МБ.")
    disk_gb: int = Field(..., ge=1, description="Диск ВМ в ГБ.")
    network_mode: VmNetworkMode = Field(default=VmNetworkMode.BRIDGE, description="bridge (разворачивается 1 раз глобально) или nat (1 раз на hub-сервер).")
    fixed_ip: IPv4Address | IPv6Address | None = Field(default=None, description="Желаемый статический IP bridge-станции (переносится в карточку ВМ).")
    number: int | None = Field(default=None, ge=0, description="Желаемый номер стенда (глобально уникален в паре servers+vm).")


class VmPresetUpdate(BaseModel):
    """Тело PATCH /vm-presets/{id} — частичное изменение пресета (только присланные поля)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    box: str | None = Field(default=None, max_length=128)
    os_version: str | None = Field(default=None, max_length=64)
    cpu: int | None = Field(default=None, ge=1)
    ram_mb: int | None = Field(default=None, ge=1)
    disk_gb: int | None = Field(default=None, ge=1)
    network_mode: VmNetworkMode | None = Field(default=None)
    fixed_ip: IPv4Address | IPv6Address | None = Field(default=None)
    number: int | None = Field(default=None, ge=0)


class VmPresetResponse(BaseModel):
    """Карточка пресета стандартной ВМ."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Preset ID (prefix vps_).")
    name: str = Field(description="Имя пресета.")
    department_id: str = Field(description="Department-владелец.")
    box: str | None = Field(default=None, description="Имя бокса-образа.")
    os_version: str | None = Field(default=None, description="Версия ОС ВМ.")
    cpu: int = Field(description="vCPU ВМ.")
    ram_mb: int = Field(description="RAM ВМ в МБ.")
    disk_gb: int = Field(description="Диск ВМ в ГБ.")
    network_mode: str = Field(description="bridge / nat.")
    fixed_ip: IPv4Address | IPv6Address | None = Field(default=None, description="Желаемый статический IP.")
    number: int | None = Field(default=None, description="Желаемый номер стенда.")
    created_at: datetime = Field(description="Когда пресет создан.")
    updated_at: datetime = Field(description="Когда пресет изменён в последний раз.")


class CreateDefaultVmItem(BaseModel):
    """Одна развёрнутая ВМ в ответе create-default-vms."""

    preset_id: str = Field(description="ID пресета, из которого развёрнута ВМ.")
    vm_id: str = Field(description="ID созданной ВМ (prefix vm_).")
    name: str = Field(description="Имя ВМ (= имя пресета).")
    task_id: str = Field(description="ID задачи vm.create воркера.")


class CreateDefaultVmSkipped(BaseModel):
    """Пропущенный пресет (уже развёрнут по правилу deploy-once)."""

    preset_id: str = Field(description="ID пропущенного пресета.")
    name: str = Field(description="Имя пресета.")
    reason: str = Field(description="Причина пропуска: already_deployed_global (bridge) / already_deployed_on_hub (nat).")


class CreateDefaultVmsResponse(BaseModel):
    """Ответ POST /servers/{id}/create-default-vms — что развёрнуто и что пропущено."""

    server_id: str = Field(description="Hub-сервер, на который разворачивали пресеты.")
    created: list[CreateDefaultVmItem] = Field(default_factory=list, description="Развёрнутые ВМ (по одной на пресет).")
    skipped: list[CreateDefaultVmSkipped] = Field(default_factory=list, description="Пресеты, пропущенные по deploy-once.")
    status: str = Field(default="queued", description="Статус: queued (задачи vm.create поставлены).")


class VmsHubTeardownResponse(BaseModel):
    """Ответ DELETE /servers/{id}/vms-hub — итог сноса VMS-hub'а (202 → VMS_HUB_TEARDOWN)."""

    server_id: str = Field(description="Сервер, снятый с роли VMS-hub.")
    task_id: str = Field(description="ID задачи vms_hub.teardown воркера (очистка хоста).")
    vms_removed: int = Field(description="Сколько карточек ВМ отдела снесено из БД (диски/снимки — каскадом).")
    status: str = Field(default="queued", description="Статус: queued.")
