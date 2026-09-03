"""Pydantic-схемы запроса/ответа для эндпоинтов /servers."""

import re
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from src.core.b64 import decode_b64 as _decode_b64
from src.core.constants import ManagementMode
from src.schemas.disk import DiskResponse, DiskSpec
from src.schemas.ipmi_controller import IpmiControllerCreate
from src.utils.url_security import validate_safe_hostname

# Allow-list имён пакетов для мутаций: без glob-метасимволов (`* ? [ ]`),
# только то, что легально для apt/dpkg/rpm — `[A-Za-z0-9._+-]`, начинается с
# alnum. Тот же класс, что в worker'е (`tasks/installed_packages._PKG_NAME_RE`).
_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")


class ServerIpmiCreate(IpmiControllerCreate):
    """IPMI-блок, вкладываемый в `ServerCreate`.

    Полностью повторяет состав `IpmiControllerCreate`: те же поля, та же парольная
    политика. Отличие — контекст: `server_id` не передаётся явно, его подставит
    `create_server`, и controller пишется в одной транзакции с сервером.
    """


def _validate_storage(disks: list[DiskSpec]) -> list[DiskSpec]:
    """Запретить дубли слотов и больше одного системного диска в одном теле."""
    slots = [d.slot for d in disks]
    if len(slots) != len(set(slots)):
        raise ValueError("duplicate disk slot in storage")
    if sum(1 for d in disks if d.is_system) > 1:
        raise ValueError("at most one system disk is allowed in storage")
    return disks


class ServerCreate(BaseModel):
    """Тело POST /servers — обязательные поля при регистрации сервера."""

    hostname: str = Field(..., max_length=255, description="Уникальное hostname сервера (FQDN, ровно один).")
    display_name: str | None = Field(default=None, max_length=256, description="Опциональное человекочитаемое имя для UI.")
    number: int | None = Field(default=None, ge=0, description="Опциональный номер стенда. Глобально уникален в паре servers+vm.")
    virtualization: bool | None = Field(default=None, description="Поддержка виртуализации (KVM). Гейт для prepare-vms-hub. Обычно ставит inventory.")
    ip_address: IPv4Address | IPv6Address = Field(description="Основной IP сервера. UNIQUE в БД (INET-тип).")
    mgmt_ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="Management IP (BMC/iDRAC), если отделён от основного.")
    ssh_port: int = Field(default=22, ge=1, le=65535, description="SSH-порт для worker-операций (default 22).")
    department_id: str = Field(description="Department-владелец сервера. Должен совпадать с department'ом caller'а, иначе 403 DEPARTMENT_ISOLATION.")
    os_version_id: str | None = Field(default=None, description="FK на os_versions. Может быть пустым до первой инвентаризации.")
    cpu_brand: str | None = Field(default=None, max_length=64, description='Производитель CPU ("Intel", "AMD", "MCST"...). Свободная строка.')
    cpu_model: str | None = Field(default=None, max_length=256, description='Модель CPU ("Xeon Silver 4314"). Свободная строка, обычно из lscpu Model name.')
    cpu_cores: int | None = Field(default=None, ge=0, description="Количество физических ядер CPU.")
    cpu_threads: int | None = Field(default=None, ge=0, description="Количество потоков CPU (с учётом SMT/HT).")
    cpu_frequency_ghz: float | None = Field(default=None, ge=0, description="Базовая частота CPU в ГГц.")
    ram_total_mb: int | None = Field(default=None, ge=0, description="Объём RAM в МБ.")
    network_interface_name: str | None = Field(default=None, max_length=64, description="Имя основного network-интерфейса (eth0, ens192, ...).")
    serial_number: str | None = Field(default=None, max_length=128, description="Серийный номер железа. UNIQUE в БД, если задан.")
    asset_tag: str | None = Field(default=None, max_length=128, description="Инвентарный номер (бирка).")
    location: str | None = Field(default=None, max_length=256, description="Физическое расположение (DC/стойка/юнит).")
    storage: list[DiskSpec] = Field(
        default_factory=list,
        description="Диски сервера: набор слотов (system/disk1/diskN) с размером в ГБ и флагом системного.",
    )
    ipmi: ServerIpmiCreate | None = Field(
        default=None,
        description=(
            "Опциональный BMC-контроллер. Если передан — создаётся атомарно "
            "вместе с сервером (тот же server_id). Управлять им дальше можно "
            "через отдельные /servers/{id}/ipmi-эндпоинты."
        ),
    )

    @field_validator("hostname")
    @classmethod
    def _check_hostname(cls, value: str) -> str:
        return validate_safe_hostname(value, field_name="hostname")

    @model_validator(mode="after")
    def _check_storage(self) -> "ServerCreate":
        _validate_storage(self.storage)
        return self


class ServerUpdate(BaseModel):
    """Тело PATCH /servers/{id}. Все поля опциональны — `model_dump(exclude_unset=True)` даёт диф."""

    hostname: str | None = Field(default=None, max_length=255, description="Сменить hostname (UNIQUE). Конфликт → 409 SERVER_DUPLICATE.")
    display_name: str | None = Field(default=None, description="Опциональное человекочитаемое имя.")
    number: int | None = Field(default=None, ge=0, description="Сменить номер стенда (UNIQUE в паре servers+vm).")
    virtualization: bool | None = Field(default=None, description="Отметить поддержку виртуализации (KVM). Гейт для prepare-vms-hub.")
    ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="Сменить основной IP.")
    mgmt_ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="Сменить management IP.")
    ssh_port: int | None = Field(default=None, ge=1, le=65535, description="Сменить SSH-порт.")
    os_version_id: str | None = Field(default=None, description="Сменить FK на os_versions.")
    cpu_brand: str | None = Field(default=None, max_length=64, description="Обновить производителя CPU.")
    cpu_model: str | None = Field(default=None, max_length=256, description="Обновить модель CPU.")
    cpu_cores: int | None = Field(default=None, ge=0, description="Обновить количество ядер CPU.")
    cpu_threads: int | None = Field(default=None, ge=0, description="Обновить количество потоков CPU.")
    cpu_frequency_ghz: float | None = Field(default=None, ge=0, description="Обновить базовую частоту CPU в ГГц.")
    ram_total_mb: int | None = Field(default=None, ge=0, description="Обновить объём RAM.")
    network_interface_name: str | None = Field(default=None, description="Сменить имя сетевого интерфейса.")
    serial_number: str | None = Field(default=None, description="Сменить serial_number (UNIQUE).")
    asset_tag: str | None = Field(default=None, description="Сменить инвентарный номер.")
    location: str | None = Field(default=None, description="Сменить физическое расположение.")
    storage: list[DiskSpec] | None = Field(
        default=None,
        description=(
            "Полная замена набора дисков. `null` (поле не прислано) — диски не "
            "трогаются; `[]` — все диски удаляются; список — синхронизация под него."
        ),
    )

    @field_validator("hostname")
    @classmethod
    def _check_hostname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_hostname(value, field_name="hostname")

    @model_validator(mode="after")
    def _check_storage(self) -> "ServerUpdate":
        if self.storage is not None:
            _validate_storage(self.storage)
        return self


class ServerAcquireRequest(BaseModel):
    """Тело POST /servers/{id}/busy — захват сервера под тест/задачу."""

    purpose: str | None = Field(
        default=None, max_length=256,
        description="Человекочитаемая метка о причине захвата (теста, сценария).",
    )


class ServerOsVersionUpdate(BaseModel):
    """Тело POST /servers/{id}/os-sync — смена os_version_id вручную."""

    os_version_id: str | None = Field(
        ...,
        description=(
            "FK на os_versions.id. `None` сбрасывает версию (например, после "
            "переустановки до первой инвентаризации)."
        ),
    )


class ServerResponse(BaseModel):
    """Карточка сервера в ответе GET/POST/PATCH /servers."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Server ID (prefix srv_).")
    hostname: str = Field(description="Уникальное hostname.")
    display_name: str | None = Field(default=None, description="Человекочитаемое имя.")
    number: int | None = Field(default=None, description="Номер стенда (глобально уникален в паре servers+vm).")
    virtualization: bool | None = Field(default=None, description="Поддержка виртуализации (KVM). None — пробы ещё не было.")
    is_vms_hub: bool = Field(default=False, description="Подготовлен ли сервер как VMS-hub (callback prepare-vms-hub).")
    vms_hub_prepared_at: datetime | None = Field(default=None, description="Когда сервер подготовлен как VMS-hub.")
    ip_address: IPv4Address | IPv6Address = Field(description="Основной IP.")
    mgmt_ip_address: IPv4Address | IPv6Address | None = Field(default=None, description="Management IP (BMC).")
    ssh_port: int = Field(description="SSH-порт.")
    os_version_id: str | None = Field(default=None, description="FK на os_versions.")
    os_last_synced_at: datetime | None = Field(default=None, description="Последняя синхронизация OS-инвентарником.")
    os_security_mode: str | None = Field(default=None, description="Режим безопасности Astra с бокса (Smolensk/Orel/Voronezh). UI склеивает с именем версии.")
    department_id: str = Field(description="Department-владелец.")
    status: str = Field(description="Статус сервера: unknown/online/offline/maintenance/decommissioned.")
    power_state: str = Field(description="Состояние питания: on/off/unknown (из кэша).")
    power_state_source: str | None = Field(default=None, description="Чем мерили last-known power_state: bmc/ping/ssh (None — пробы ещё не было).")
    power_state_checked_at: datetime | None = Field(default=None, description="Когда воркер последний раз пробовал питание (UTC; None — пробы ещё не было).")
    ping_reachable: bool | None = Field(default=None, description="Отвечает ли сервер на ping (доступность в списке ведётся по нему; None — пробы ещё не было).")
    ping_latency_ms: float | None = Field(default=None, description="RTT ping в миллисекундах (None — недоступен или пробы ещё не было).")
    ping_checked_at: datetime | None = Field(default=None, description="Когда последний раз пробовали ping (UTC; None — пробы ещё не было).")
    ssh_reachable: bool | None = Field(default=None, description="Доступен ли SSH-порт (None — пробы ещё не было).")
    ssh_latency_ms: float | None = Field(default=None, description="Задержка SSH-пробы в миллисекундах (None — недоступен или пробы ещё не было).")
    ssh_checked_at: datetime | None = Field(default=None, description="Когда последний раз пробовали SSH (UTC; None — пробы ещё не было).")
    ipmi_power_state: str | None = Field(default=None, description="Питание по BMC/IPMI: on/off/unknown (None — пробы ещё не было).")
    ipmi_checked_at: datetime | None = Field(default=None, description="Когда последний раз опрашивали питание по BMC (UTC; None — пробы ещё не было).")
    busy_state: str = Field(description="Состояние занятости: free/busy/testing.")
    busy_user_id: str | None = Field(default=None, description="user_id того, кто взял сервер (если busy/testing).")
    busy_since: datetime | None = Field(default=None, description="С какого момента сервер занят.")
    busy_note: str | None = Field(default=None, description="Произвольная метка о причине занятости.")
    serial_number: str | None = Field(default=None, description="Серийный номер железа.")
    asset_tag: str | None = Field(default=None, description="Инвентарный номер.")
    location: str | None = Field(default=None, description="Физическое расположение.")
    cpu_brand: str | None = Field(default=None, description="Производитель CPU.")
    cpu_model: str | None = Field(default=None, description="Модель CPU.")
    cpu_cores: int | None = Field(default=None, description="Физические ядра CPU.")
    cpu_threads: int | None = Field(default=None, description="Потоки CPU.")
    cpu_frequency_ghz: float | None = Field(default=None, description="Базовая частота CPU в ГГц.")
    ram_total_mb: int | None = Field(default=None, description="RAM в МБ.")
    network_interface_name: str | None = Field(default=None, description="Имя основного сетевого интерфейса.")
    network_interfaces: list[str] = Field(
        default_factory=list,
        description="Все активные сетевые интерфейсы с последней инвентаризации (без lo).",
    )
    decommissioned_at: datetime | None = Field(default=None, description="Когда сервер выведен из эксплуатации.")
    is_managed: bool = Field(default=False, description="Прошёл ли сервер бутстрап управления (prepare).")
    management_user: str | None = Field(default=None, description="Имя управляющего пользователя DBOS (после prepare).")
    management_mode: ManagementMode | None = Field(default=None, description="Детектнутая при prepare редакция ОС / режим создания управляющей учётки.")
    prepared_at: datetime | None = Field(default=None, description="Когда сервер подготовлен к управлению (prepare callback).")
    mgmt_ssh_public_key: str | None = Field(default=None, description="Публичный SSH-ключ управляющей учётки (per-server). Приватный ключ и пароль не отдаются.")
    mgmt_creds_rotated_at: datetime | None = Field(default=None, description="Когда управляющие креды последний раз ротированы.")
    mgmt_creds_pending_apply: bool = Field(default=False, description="Идёт применение новой управляющей пары на боксе (ротация ещё не подтверждена).")
    storage: list[DiskResponse] = Field(default_factory=list, description="Диски сервера (slot/size_gb/is_system/model).")
    created_at: datetime = Field(description="Когда карточка создана.")
    updated_at: datetime = Field(description="Когда карточка изменена в последний раз.")
    created_by: str | None = Field(default=None, description="user_id, создавший карточку.")

    @field_validator("network_interfaces", mode="before")
    @classmethod
    def _coerce_network_interfaces(cls, value):
        # В БД колонка nullable JSONB: до первой инвентаризации там None.
        # model_validate(server) прочитает атрибут как есть — приводим к [].
        return value or []

    @computed_field
    @property
    def ram_total_gb(self) -> float | None:
        """RAM в ГБ — производная от `ram_total_mb` для удобства UI. None, если
        объём не известен."""
        if self.ram_total_mb is None:
            return None
        return round(self.ram_total_mb / 1024, 1)

    @classmethod
    def from_server(cls, server, disks) -> "ServerResponse":
        """Собрать карточку из ORM-сервера + явно загруженного списка дисков.

        Диски передаются отдельно, а не через lazy-relationship — async-сессия
        не подгружает их автоматически при сериализации.
        """
        resp = cls.model_validate(server)
        resp.storage = [DiskResponse.from_orm_disk(d) for d in disks]
        return resp


class ServerTaskDispatchResponse(BaseModel):
    """Стандартный ответ на dispatch worker-task'и (`{task_id, status}`).

    Используется live BMC-probe (`POST /servers/{id}/power/status` →
    `power.status`), full inventory-sync (`POST /servers/{id}/inventory/sync`
    → `inventory.sync`) и остальными dispatch'ами. Структура одинаковая,
    поэтому держим одну общую схему вместо per-task-kind дубликатов.
    """

    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(description="Статус: queued.")


# Legacy alias — старые endpoint'ы и тесты импортируют исторический имя.
ServerPowerStatusDispatchResponse = ServerTaskDispatchResponse


class BulkInstalledPackagesRequest(BaseModel):
    """Тело POST /servers/installed-packages/bulk — массовый live-запрос пакетов.

    `server_ids` — список серверов, для каждого диспатчится отдельная
    `installed_packages.list` (тот же per-server путь, что у одиночного
    эндпоинта). Паттерны общие для всего батча — shell-glob, валидируются
    тем же allow-list'ом, что и одиночный запрос.

    Паттернов можно прислать несколько (`patterns: ["ssh*", "bash*", "*libs*"]`)
    — пакет на сервере попадёт в результат, если матчит ЛЮБОЙ из них (OR).
    Одиночный `pattern` оставлен для обратной совместимости: если прислан
    только он — эквивалент `patterns=[pattern]`. Если присланы оба, выигрывает
    `patterns`, одиночный `pattern` игнорируется. Ни одного из полей — дефолт
    `["*"]` (все пакеты). Эффективный список достаём через `effective_patterns()`.
    """

    model_config = ConfigDict(extra="forbid")

    server_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Серверы для запроса пакетов. Дубли схлопываются, порядок сохраняется.",
    )
    pattern: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "Одиночный shell-glob паттерн (`htop`, `linux-image*`, `*-dev`). "
            "Back-compat: эквивалент `patterns=[pattern]`. Если задан вместе с "
            "`patterns`, игнорируется (приоритет у `patterns`)."
        ),
    )
    patterns: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description=(
            "Несколько shell-glob паттернов (`[\"ssh*\", \"bash*\", \"*libs*\"]`). "
            "Пакет попадает в результат, если матчит ЛЮБОЙ (OR-матч). Каждый "
            "элемент валидируется тем же allow-list'ом, что одиночный `pattern`. "
            "1..20 паттернов. Приоритетнее одиночного `pattern`."
        ),
    )

    @field_validator("patterns")
    @classmethod
    def _check_patterns_each(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for p in value:
            if not p or len(p) > 128:
                raise ValueError("each pattern must be 1..128 chars long")
        return value

    def effective_patterns(self) -> list[str]:
        """Свести `patterns`/`pattern` к итоговому списку glob'ов.

        Приоритет: `patterns` (если присланы) → `[pattern]` (back-compat) →
        `["*"]` (дефолт). Дедуп с сохранением порядка — повторный паттерн в
        теле даёт одну ветку OR-матча.
        """
        if self.patterns is not None:
            source = self.patterns
        elif self.pattern is not None:
            source = [self.pattern]
        else:
            source = ["*"]
        seen: set[str] = set()
        out: list[str] = []
        for p in source:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out


class BulkInstalledPackagesServerResult(BaseModel):
    """Результат запроса пакетов по одному серверу в составе bulk-ответа.

    `status` говорит UI, отдал ли сервер данные:
    * `ok` — задача `installed_packages.list` поставлена, `task_id` заполнен;
      сам список пакетов UI добирает поллингом `GET /tasks/{task_id}`.
    * `prepare_required` — сервер не прошёл prepare, SSH-probe невозможна.
    * `decommissioned` — сервер списан, worker-операции не принимает.
    * `not_found` — сервер не виден (cross-dept / нет такого id).
    * `auth_failed` — dispatch не дошёл до воркера (broker недоступен).

    `packages` на момент dispatch'а всегда пуст — это контракт async-модели:
    реальные `{name, version}` приходят в `task.result`, UI раскладывает их
    в pivot «пакеты×серверы» либо «серверы×пакеты» сам.
    """

    model_config = ConfigDict(from_attributes=True)

    server_id: str = Field(description="ID сервера (prefix srv_).")
    hostname: str | None = Field(default=None, description="Hostname сервера (None для невидимого).")
    os_version_id: str | None = Field(default=None, description="FK на os_versions (None до инвентаризации).")
    status: str = Field(description="ok / prepare_required / decommissioned / not_found / auth_failed.")
    task_id: str | None = Field(default=None, description="ID задачи воркера, если status=ok.")
    packages: list[dict] = Field(
        default_factory=list,
        description="Пусто на dispatch; UI добирает `{name, version}` из task.result по task_id.",
    )


class BulkInstalledPackagesResponse(BaseModel):
    """Сводный ответ массового запроса пакетов.

    Плоский per-server список — пригоден и для pivot'а «пакеты×серверы», и
    «серверы×пакеты». `requested`/`dispatched` дают UI быстрый счётчик «сколько
    серверов реально отдадут данные» без обхода всего массива.
    """

    pattern: str = Field(
        description=(
            "Первый применённый glob-паттерн. Оставлен для обратной "
            "совместимости; при нескольких паттернах смотри `patterns`."
        ),
    )
    patterns: list[str] = Field(
        default_factory=list,
        description="Все применённые glob-паттерны (OR-матч), в порядке запроса.",
    )
    requested: int = Field(description="Сколько уникальных серверов запрошено.")
    dispatched: int = Field(description="Сколько серверов реально получили задачу (status=ok).")
    results: list[BulkInstalledPackagesServerResult] = Field(
        description="Per-server результат, порядок соответствует входному списку.",
    )


class BulkPackagesActionRequest(BaseModel):
    """Тело POST /servers/packages/bulk-action — массовая мутация пакетов.

    `action` — `install` / `remove` / `update`. Для install/remove `packages`
    обязателен и непуст. Для update `packages` опционален: пустой/отсутствует =
    обновить все доступные пакеты, иначе обновить только перечисленные. Имена
    пакетов валидируются строгим allow-list'ом `[A-Za-z0-9._+-]` (без glob —
    устанавливать/сносить по маске нельзя). На каждый сервер диспатчится
    отдельная `installed_packages.<action>`.
    """

    model_config = ConfigDict(extra="forbid")

    server_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Серверы для операции. Дубли схлопываются, порядок сохраняется.",
    )
    action: Literal["install", "remove", "update"] = Field(
        ...,
        description="install / remove / update.",
    )
    packages: list[str] = Field(
        default_factory=list,
        description=(
            "Имена пакетов. Обязательны для install/remove; для update "
            "пусто = обновить всё. Allow-list [A-Za-z0-9._+-], без glob."
        ),
    )

    @model_validator(mode="after")
    def _validate_action_packages(self) -> "BulkPackagesActionRequest":
        if self.action in ("install", "remove") and not self.packages:
            raise ValueError(
                f"packages must be a non-empty list for action '{self.action}'"
            )
        bad = [p for p in self.packages if not _PKG_NAME_RE.match(p)]
        if bad:
            raise ValueError(
                f"invalid package name(s) {bad!r}; allowed [A-Za-z0-9._+-], "
                "must start with an alphanumeric"
            )
        return self


class BulkPackagesActionServerResult(BaseModel):
    """Результат мутации пакетов по одному серверу в составе bulk-ответа.

    `status`:
    * `ok` — задача `installed_packages.<action>` поставлена, `task_id` есть;
      реальный результат UI добирает поллингом `GET /tasks/{task_id}`.
    * `prepare_required` — сервер не прошёл prepare, SSH-мутация невозможна.
    * `reserved` — сервер занят (busy) другим оператором, деструктив отбит.
    * `acs_busy` — сервер занят ACS-снимком/восстановлением, звонящий не админ.
    * `decommissioned` — сервер списан, worker-операции не принимает.
    * `not_found` — сервер не виден (cross-dept / нет такого id).
    * `queued` — синоним ok для симметрии с другими bulk-ответами; не
      используется, оставлен в контракте для совместимости.
    """

    model_config = ConfigDict(from_attributes=True)

    server_id: str = Field(description="ID сервера (prefix srv_).")
    hostname: str | None = Field(default=None, description="Hostname (None для невидимого).")
    status: str = Field(description="ok / prepare_required / reserved / acs_busy / decommissioned / not_found / queued.")
    task_id: str | None = Field(default=None, description="ID задачи воркера, если status=ok.")


class BulkPackagesActionResponse(BaseModel):
    """Сводный ответ массовой мутации пакетов.

    `requested`/`dispatched` дают UI быстрый счётчик «сколько серверов реально
    получили задачу» без обхода всего массива results.
    """

    action: str = Field(description="Применённое действие: install / remove / update.")
    packages: list[str] = Field(description="Имена пакетов (пусто при update всего).")
    requested: int = Field(description="Сколько уникальных серверов запрошено.")
    dispatched: int = Field(description="Сколько серверов реально получили задачу (status=ok).")
    results: list[BulkPackagesActionServerResult] = Field(
        description="Per-server результат, порядок соответствует входному списку.",
    )


class PackageHistoryEntry(BaseModel):
    """Один прошлый live-запрос пакетов сервера (история без повторного теста).

    Источник — row из `dev_server_worker.tasks` с
    `task_kind=installed_packages.list` и `target_server_id=сервер`.
    `pattern`/`patterns` берутся из task-payload'а (что запрашивали),
    `packages`/`package_count` — из `task.result` (что worker нашёл). Поля
    времени, инициатора и статуса читаются прямо с task-row'а. Запрос ещё в
    полёте (`queued`/`running`) — `packages`/`package_count` пусты, появятся
    при финализации задачи.
    """

    task_id: str = Field(description="task_id запроса (PK в dev_server_worker.tasks).")
    status: str = Field(description="queued / running / succeeded / failed / cancelled.")
    pattern: str | None = Field(
        default=None,
        description=(
            "Запрошенный glob-паттерн (raw, как ввёл оператор). None у row без "
            "одиночного pattern в payload."
        ),
    )
    patterns: list[str] | None = Field(
        default=None,
        description="Список glob-паттернов (OR-матч). None если в payload их не было.",
    )
    requested_by: str | None = Field(
        default=None,
        description="user_id инициатора (task.created_by). None у dispatch'ей без актора.",
    )
    requested_at: datetime = Field(description="Момент постановки запроса (enqueued_at).")
    finished_at: datetime | None = Field(
        default=None,
        description="Момент завершения (completed_at). None пока запрос не терминальный.",
    )
    package_count: int | None = Field(
        default=None,
        description="Сколько пакетов нашёл worker (len result.packages). None если результата ещё нет.",
    )
    packages: list[dict] | None = Field(
        default=None,
        description=(
            "Найденные пакеты `{name, version}` из task.result. None пока запрос "
            "не завершён или result не содержит списка."
        ),
    )
    last_error: str | None = Field(
        default=None, description="Текст ошибки для failed-запросов.",
    )


class ServerAstraUpdateRequest(BaseModel):
    """Тело POST /servers/{id}/astra-update — обновление ОС до версии каталога.

    `os_version_id` — целевая версия из глобального каталога `OsVersion`.
    server_service берёт её `repositories`, полностью перезаписывает ими
    `/etc/apt/sources.list` на сервере и гонит `apt update && astra-update`.
    Версия обязана существовать и иметь непустой список репозиториев — иначе
    422 / 409 ещё до постановки задачи.
    """

    os_version_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Целевая OS-версия из каталога (prefix osv_).",
    )


class ServerAcsSnapshotCreateRequest(BaseModel):
    """Тело POST /servers/{id}/acs-snapshots — создать снимок диска через ACS.

    `os_version_id` — версия каталога, под которой создаётся снимок (для
    именования/группировки снимков по РЦ; сам снимок делается не через apt,
    репозитории версии тут не участвуют). Версия обязана существовать —
    иначе 404 до постановки задачи.
    """

    os_version_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Версия каталога ОС, под которой создаётся снимок (prefix osv_).",
    )


class ServerAcsSnapshotRestoreRequest(BaseModel):
    """Тело POST /servers/{id}/acs-snapshots/restore — восстановить снимок через ACS.

    `os_version_id` — версия каталога, к снимку которой откатываемся; тем же
    полем резолвится bootstrap-пароль (`os_version_bootstrap_passwords`) для
    авто-`server.prepare`, который стартует сразу после успешного restore.
    Восстановление — полная перезапись диска, необратимо.
    """

    os_version_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Версия каталога ОС, к снимку которой восстанавливаемся (prefix osv_).",
    )


class ServerPrepareRequest(BaseModel):
    """Тело POST /servers/{id}/prepare — bootstrap-креды для онбординга.

    Два взаимоисключающих режима выбора bootstrap-кред:

    * **выбор аккаунта** — `{account_id}`. server_service сам резолвит
      привязанный к серверу `server_account`, расшифровывает его пароль и
      кладёт в Redis как bootstrap. UI пароль НЕ шлёт — он живёт только в БД
      в зашифрованном виде. Аккаунт обязан быть привязан к этому серверу и
      доступен вызывающему (право `view_password`).
    * **ручной ввод** — `{username_b64, password_b64, ssh_private_key_b64?}`.
      Логин и пароль приходят в base64 (симметрия с reveal-картами).
      Опциональный `ssh_private_key_b64` — приватный SSH-ключ bootstrap-
      аккаунта, если вход на свежий бокс идёт по ключу, а не паролю.

    Ровно один режим: указать `account_id` вместе с ручными полями — 422.
    Не указать ничего — 422.

    Креды одноразовые — они НЕ хранятся персистентно: воркер заходит под
    ними по SSH, заводит управляющего пользователя DBOS и кладёт ему
    публичный ключ, после чего исходные креды больше не нужны.

    Ручной bootstrap-пароль под усиленную политику НЕ гоняется — это
    существующий пароль хоста (на дефолт-образах бывает и `u`/`1`), а не
    новый пароль, который мы задаём. Требовать его надёжным неверно: тогда
    нельзя подготовить сервер со слабым текущим паролем. Надёжность —
    только у генерируемых при prepare управляющих кред и у управляемых
    DBOS-аккаунтов (`ServerAccountCreate.password_b64`, `PasswordRotateRequest`,
    `IpmiCredentialsRotatedRequest.new_password`).
    """

    account_id: str | None = Field(
        default=None,
        description=(
            "Привязанный к серверу server_account, чьи креды использовать "
            "как bootstrap. Взаимоисключающе с username_b64/password_b64."
        ),
    )
    username_b64: str | None = Field(
        default=None,
        description="Логин bootstrap-аккаунта в base64 (UTF-8 после декода).",
    )
    password_b64: str | None = Field(
        default=None,
        description="Пароль bootstrap-аккаунта в base64 (UTF-8 после декода).",
    )
    ssh_private_key_b64: str | None = Field(
        default=None,
        description=(
            "Опциональный приватный SSH-ключ bootstrap-аккаунта в base64 "
            "(PEM/OpenSSH). Только для ручного режима."
        ),
    )

    @field_validator("username_b64")
    @classmethod
    def _check_username_b64(cls, value: str | None) -> str | None:
        if value is None:
            return value
        _decode_b64(value, "username_b64")
        return value

    @field_validator("password_b64")
    @classmethod
    def _check_password_b64(cls, value: str | None) -> str | None:
        if value is None:
            return value
        # Это существующий пароль хоста для первичного захода (bootstrap), а не
        # новый пароль, который мы задаём. Политику надёжности к нему не
        # применяем — иначе нельзя подготовить сервер, чей текущий пароль слабее
        # политики. Валидируем только декодируемость base64.
        _decode_b64(value, "password_b64")
        return value

    @field_validator("ssh_private_key_b64")
    @classmethod
    def _check_ssh_private_key_b64(cls, value: str | None) -> str | None:
        if value is None:
            return value
        # Приватный ключ может быть бинарным/не-UTF-8 в теории, но OpenSSH/PEM
        # ключи — всегда ASCII-текст. Декодируем строго тем же b64-валидатором.
        _decode_b64(value, "ssh_private_key_b64")
        return value

    @model_validator(mode="after")
    def _check_exactly_one_mode(self) -> "ServerPrepareRequest":
        manual_fields = [self.username_b64, self.password_b64]
        manual = any(f is not None for f in manual_fields)
        account = self.account_id is not None
        if account and (manual or self.ssh_private_key_b64 is not None):
            raise ValueError(
                "provide either account_id or manual credentials, not both"
            )
        if account:
            return self
        # Ручной режим: и логин, и пароль обязательны.
        if self.username_b64 is None or self.password_b64 is None:
            raise ValueError(
                "manual mode requires both username_b64 and password_b64; "
                "or pass account_id to use a linked server_account"
            )
        return self

    def is_account_mode(self) -> bool:
        return self.account_id is not None

    def username(self) -> str:
        """Декодированный логин ручного режима (валидность уже проверена)."""
        assert self.username_b64 is not None
        return _decode_b64(self.username_b64, "username_b64")

    def password(self) -> str:
        """Декодированный пароль ручного режима (валидность уже проверена)."""
        assert self.password_b64 is not None
        return _decode_b64(self.password_b64, "password_b64")

    def ssh_private_key(self) -> str | None:
        """Декодированный приватный SSH-ключ ручного режима (или None)."""
        if self.ssh_private_key_b64 is None:
            return None
        return _decode_b64(self.ssh_private_key_b64, "ssh_private_key_b64")


class ServerPrepareResponse(BaseModel):
    """Ответ на dispatch `server.prepare` — task_id онбординг-задачи."""

    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(description="Статус: queued.")


class ServerManagementCredsRotateResponse(BaseModel):
    """Ответ на dispatch `server.rotate_management_creds` — task_id ротации (#3)."""

    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(description="Статус: queued.")


class ServerPrepareBulkItem(BaseModel):
    """Один сервер в массовом prepare — server_id + ручные bootstrap-креды.

    В отличие от single-prepare account-режима тут только ручной ввод:
    у разных боксов в батче разные исходные креды (свежая поставка ОС,
    каждый со своим root-паролем). Логин/пароль приходят в base64 — та же
    политика и тот же декод, что у `ServerPrepareRequest` (минимум 16
    символов, буква+цифра+символ). Опциональный `ssh_private_key_b64` —
    если вход на бокс идёт по ключу.
    """

    server_id: str = Field(description="Сервер, который готовим (prefix srv_).")
    username_b64: str = Field(
        description="Логин bootstrap-аккаунта в base64 (UTF-8 после декода).",
    )
    password_b64: str = Field(
        description="Пароль bootstrap-аккаунта в base64 (UTF-8 после декода).",
    )
    ssh_private_key_b64: str | None = Field(
        default=None,
        description=(
            "Опциональный приватный SSH-ключ bootstrap-аккаунта в base64 "
            "(PEM/OpenSSH)."
        ),
    )

    @field_validator("username_b64")
    @classmethod
    def _check_username_b64(cls, value: str) -> str:
        _decode_b64(value, "username_b64")
        return value

    @field_validator("password_b64")
    @classmethod
    def _check_password_b64(cls, value: str) -> str:
        # Существующий пароль хоста для bootstrap, не новый — надёжность не
        # проверяем (см. ServerPrepareRequest.password_b64).
        _decode_b64(value, "password_b64")
        return value

    @field_validator("ssh_private_key_b64")
    @classmethod
    def _check_ssh_private_key_b64(cls, value: str | None) -> str | None:
        if value is None:
            return value
        _decode_b64(value, "ssh_private_key_b64")
        return value

    def username(self) -> str:
        return _decode_b64(self.username_b64, "username_b64")

    def password(self) -> str:
        return _decode_b64(self.password_b64, "password_b64")

    def ssh_private_key(self) -> str | None:
        if self.ssh_private_key_b64 is None:
            return None
        return _decode_b64(self.ssh_private_key_b64, "ssh_private_key_b64")


class ServerPrepareBulkRequest(BaseModel):
    """Тело POST /servers/prepare/bulk — массовый онбординг.

    Список `items` — по записи на сервер, у каждого свои bootstrap-креды.
    Дубли server_id в одном теле запрещены (422): иначе один битый сервер
    в двух экземплярах путал бы per-server-итог. Cap на длину списка
    задаётся `BULK_PREPARE_MAX_SERVERS`; превышение → 413.
    """

    items: list[ServerPrepareBulkItem] = Field(
        ..., min_length=1,
        description="Серверы для подготовки, по одному элементу на сервер.",
    )

    @model_validator(mode="after")
    def _check_unique_servers(self) -> "ServerPrepareBulkRequest":
        ids = [it.server_id for it in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate server_id in bulk prepare items")
        return self


class ServerPrepareBulkResult(BaseModel):
    """Per-server исход массового prepare.

    `status=queued` — задача поставлена, `task_id` заполнен. `status=skipped`
    — сервер не подготовлен (нет прав / cross-dept / списан / уже-в-очереди /
    worker недоступен), `reason` несёт причину, `task_id` пуст.
    """

    server_id: str = Field(description="Сервер из запроса.")
    status: str = Field(description="queued | skipped.")
    task_id: str | None = Field(
        default=None, description="ID задачи воркера (prefix tsk_), если queued.",
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Причина пропуска (для skipped): permission_denied / "
            "not_found_or_cross_dept / decommissioned / idempotent_conflict / "
            "worker_unreachable."
        ),
    )


class ServerPrepareBulkResponse(BaseModel):
    """Ответ массового prepare — per-server результаты + сводные счётчики."""

    results: list[ServerPrepareBulkResult] = Field(
        description="Исход по каждому серверу из запроса (в порядке items).",
    )
    queued_count: int = Field(description="Сколько задач поставлено.")
    skipped_count: int = Field(description="Сколько серверов пропущено.")


class ServerPrepareBatchItem(ServerPrepareRequest):
    """Один сервер в массовом prepare с выбором режима кред (account / manual).

    Наследует ровно те же поля и валидаторы, что single-prepare
    (`ServerPrepareRequest`): `account_id` (режим выбора привязанной учётки,
    по умолчанию) ЛИБО `username_b64`+`password_b64`(+`ssh_private_key_b64`)
    (ручной режим). Ровно один режим на сервер — нарушение → 422. Добавляет
    только `server_id`.
    """

    server_id: str = Field(description="Сервер, который готовим (prefix srv_).")


class ServerPrepareBatchRequest(BaseModel):
    """Тело POST /servers/prepare-batch — массовый онбординг с per-server режимом.

    Список `items` — по записи на сервер, у каждого свой выбор bootstrap-кред
    (account-режим по умолчанию, ручной опционально). Дубли server_id в одном
    теле запрещены (422). Cap на длину списка — `BULK_PREPARE_MAX_SERVERS`;
    превышение → 413.
    """

    items: list[ServerPrepareBatchItem] = Field(
        ..., min_length=1,
        description="Серверы для подготовки, по одному элементу на сервер.",
    )

    @model_validator(mode="after")
    def _check_unique_servers(self) -> "ServerPrepareBatchRequest":
        ids = [it.server_id for it in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate server_id in prepare-batch items")
        return self


class ServerBatchDispatched(BaseModel):
    """Одна успешно поставленная per-server задача в batch-ответе."""

    server_id: str = Field(description="Сервер, на который ушла задача.")
    server_name: str | None = Field(
        default=None, description="Имя сервера (display_name либо hostname).",
    )
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(
        default="dispatched", description="Статус диспетчеризации (dispatched).",
    )


class ServerBatchFailed(BaseModel):
    """Сервер, на который задача не поставлена (пропуск в batch-ответе)."""

    server_id: str = Field(description="Сервер, для которого dispatch не выполнен.")
    server_name: str | None = Field(
        default=None, description="Имя сервера (display_name либо hostname); None для невидимого.",
    )
    reason: str = Field(
        description=(
            "Причина: not_found_or_cross_dept | decommissioned | "
            "idempotent_conflict | idempotency_key_reuse_conflict | "
            "account_has_no_password | account_not_found | account_not_linked | "
            "permission_denied | worker_unreachable | not_attempted."
        ),
    )


class ServerPrepareBatchResponse(BaseModel):
    """Ответ массового prepare-batch — per-server dispatched/failed + batch_id.

    Форма симметрична mass-rotation: `batch_id` — сквозной идентификатор батча,
    `dispatched` — успешно поставленные задачи, `failed` — серверы с причиной
    пропуска. Один битый сервер не валит остальной батч.
    """

    batch_id: str = Field(description="ID батча (prefix bat_).")
    dispatched: list[ServerBatchDispatched] = Field(
        default_factory=list,
        description="Успешно поставленные задачи (task_id / server_id / server_name).",
    )
    failed: list[ServerBatchFailed] = Field(
        default_factory=list,
        description="Серверы, на которые задача не поставлена, с причиной в `reason`.",
    )


class ServerAcsSnapshotBatchRequest(BaseModel):
    """Тело POST /servers/acs-snapshots/{create,restore}-batch.

    В отличие от prepare-batch, кред тут нет — только список серверов и одна
    версия каталога ОС на весь батч (снимок создаётся/восстанавливается под
    одной и той же РЦ для всех выбранных серверов). Фронт сам исключает
    VMS-hub серверы из `server_ids` чекбоксом "выбрать все, кроме VMS-hub" —
    бэкенд всё равно отбивает затесавшийся hub per-server (`SERVER_IS_VMS_HUB`),
    без доп. логики под сам чекбокс. Дубли `server_id` → 422.
    """

    server_ids: list[str] = Field(
        ..., min_length=1,
        description="Серверы, на которые ставится задача, по одному task на сервер.",
    )
    os_version_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Версия каталога ОС, под которой создаётся/восстанавливается снимок (prefix osv_).",
    )

    @model_validator(mode="after")
    def _check_unique_servers(self) -> "ServerAcsSnapshotBatchRequest":
        if len(self.server_ids) != len(set(self.server_ids)):
            raise ValueError("duplicate server_id in acs-snapshot batch")
        return self


class ServerAcsSnapshotBatchResponse(BaseModel):
    """Ответ batch create/restore ACS — per-server dispatched/failed + batch_id.

    Форма идентична `ServerPrepareBatchResponse` — переиспользуем те же
    per-server элементы (`ServerBatchDispatched`/`ServerBatchFailed`).
    """

    batch_id: str = Field(description="ID батча (prefix bat_).")
    dispatched: list[ServerBatchDispatched] = Field(
        default_factory=list,
        description="Успешно поставленные задачи (task_id / server_id / server_name).",
    )
    failed: list[ServerBatchFailed] = Field(
        default_factory=list,
        description="Серверы, на которые задача не поставлена, с причиной в `reason`.",
    )


class AcsSnapshotItem(BaseModel):
    """Один снимок ACS, принадлежащий конкретному серверу (по префиксу имени)."""

    name: str = Field(description="Полное имя снимка на ACS (`{hostname}-{version_name}`).")
    version_name: str = Field(description="Хвост имени после `{hostname}-` — версия РЦ снимка.")


class AcsSnapshotListResponse(BaseModel):
    """Ответ GET /servers/{id}/acs-snapshots — снимки этого сервера на ACS."""

    snapshots: list[AcsSnapshotItem] = Field(
        default_factory=list,
        description="Снимки этого сервера (по префиксу hostname), отсортированы по имени.",
    )


class AcsAvailabilityResponse(BaseModel):
    """Ответ GET /servers/{id}/acs-availability — можно ли показывать снимки ACS."""

    available: bool = Field(
        description=(
            "true — есть право acs_snapshot (list/create/restore разом), ACS "
            "включён платформенно и для отдела сервера. false в любом из "
            "противоположных случаев."
        ),
    )


class ServerCleanRequest(BaseModel):
    """Тело POST /servers/{id}/clean — оркестрация очистки после переустановки ОС.

    Пять независимых флагов из модалки. Выбранные действия выполняются в
    фиксированном порядке: `unbind_accounts` → `rerun_prepare` →
    `update_os_version` → `run_inventory_sync` → `delete_vms`.

    * `unbind_accounts` — отвязать ВСЕ привязанные учётки сервера (снять связки
      + userdel-fanout на боксы, где учётка стояла).
    * `rerun_prepare` — заново забутстрапить управление. Требует блок `prepare`
      с bootstrap-кредами (те же поля, что single-prepare: `account_id` —
      привязанная учётка по умолчанию, либо ручной `username_b64`/`password_b64`
      /`ssh_private_key_b64`).
    * `update_os_version` — ручная смена `os_version_id` (значение в
      `os_version_id`; `null` сбрасывает версию).
    * `run_inventory_sync` — поставить `inventory.sync` (SSH-probe, требует
      prepared-сервер).
    * `delete_vms` — снести все ВМ этого хаба: переустановка ОС стёрла их
      qcow2-диски, записи становятся orphan'ами. Row-only каскад, без диспатча
      на хаб.

    Хотя бы один флаг должен быть выбран (иначе 422).
    """

    unbind_accounts: bool = Field(
        default=False, description="Отвязать все привязанные учётки сервера.",
    )
    update_os_version: bool = Field(
        default=False, description="Сменить os_version_id вручную (значение в `os_version_id`).",
    )
    rerun_prepare: bool = Field(
        default=False, description="Заново забутстрапить управление (требует блок `prepare`).",
    )
    run_inventory_sync: bool = Field(
        default=False, description="Поставить inventory.sync (SSH-probe).",
    )
    delete_vms: bool = Field(
        default=False,
        description=(
            "Снести все ВМ этого хаба: переустановка ОС стёрла их qcow2-диски, "
            "записи становятся orphan'ами. Row-only каскад, без диспатча на хаб."
        ),
    )
    os_version_id: str | None = Field(
        default=None,
        description=(
            "FK на os_versions для `update_os_version`. `null` сбрасывает версию. "
            "Учитывается только если `update_os_version=true`."
        ),
    )
    prepare: ServerPrepareRequest | None = Field(
        default=None,
        description=(
            "Bootstrap-креды для `rerun_prepare` (обязателен, если "
            "`rerun_prepare=true`). Те же поля и режимы, что single-prepare."
        ),
    )

    @model_validator(mode="after")
    def _check_actions(self) -> "ServerCleanRequest":
        if self.rerun_prepare and self.prepare is None:
            raise ValueError(
                "rerun_prepare=true requires bootstrap credentials in 'prepare'"
            )
        if not (
            self.unbind_accounts
            or self.update_os_version
            or self.rerun_prepare
            or self.run_inventory_sync
            or self.delete_vms
        ):
            raise ValueError("at least one clean action must be selected")
        return self


class ServerCleanActionResult(BaseModel):
    """Per-action итог clean'а.

    `status`: `done` (синхронное действие выполнено: unbind / os-version),
    `dispatched` (task поставлена: prepare / inventory; `task_id` заполнен),
    `skipped` (действие не выбрано — `reason=not_selected`), `failed`
    (`reason` несёт причину).
    """

    status: str = Field(description="done | dispatched | skipped | failed.")
    task_id: str | None = Field(
        default=None, description="ID задачи воркера, если status=dispatched.",
    )
    reason: str | None = Field(
        default=None, description="Причина для skipped/failed.",
    )
    detail: dict | None = Field(
        default=None, description="Доп. детали (например счётчики unbind'а).",
    )


class ServerCleanResponse(BaseModel):
    """Сводный ответ POST /servers/{id}/clean — per-action итоги."""

    server_id: str = Field(description="Сервер, который чистили.")
    unbind_accounts: ServerCleanActionResult
    rerun_prepare: ServerCleanActionResult
    update_os_version: ServerCleanActionResult
    run_inventory_sync: ServerCleanActionResult
    delete_vms: ServerCleanActionResult


class ServerPrepareCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{id}/prepared — callback воркера.

    Воркер сообщает, что онбординг завершён: управляющий пользователь заведён
    и публичный ключ положен. server_service помечает сервер подготовленным
    (`is_managed=True`, `prepared_at`, имя управляющего юзера).
    """

    management_user: str = Field(
        ..., min_length=1, max_length=64,
        pattern=r"^[A-Za-z0-9._\-]+$",
        description="Имя заведённого управляющего пользователя DBOS.",
    )
    management_mode: ManagementMode | None = Field(
        default=None,
        description=(
            "Детектнутая на боксе редакция ОС / режим создания учётки. "
            "Не прислан (старый воркер) — server_service оставит прежнее значение."
        ),
    )


class ServerPrepareCallbackResponse(BaseModel):
    """Подтверждение записи prepared-callback'а."""

    ok: bool = True
    is_managed: bool = Field(description="Текущее значение флага управляемости.")
    prepared_at: str | None = Field(
        default=None, description="ISO-8601 UTC момент подготовки.",
    )


class ServerAstraUpdateCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{id}/astra-updated — callback воркера.

    Воркер сообщает исход обновления ОС. `succeeded=True` — `apt update &&
    astra-update` отработали: server_service привязывает сервер к целевой
    версии (`os_version_id`), снимает updating-блокировку и запускает
    inventory.sync. `succeeded=False` — обновление упало: блокировку снимаем
    (сервер снова доступен операторам), версию не трогаем, inventory не гоним.
    """

    os_version_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Целевая OS-версия, до которой обновлялись (prefix osv_).",
    )
    succeeded: bool = Field(
        description="True — обновление прошло; False — упало (снять блокировку).",
    )


class ServerAstraUpdateCallbackResponse(BaseModel):
    """Подтверждение записи astra-updated callback'а."""

    ok: bool = True
    os_version_id: str | None = Field(
        default=None,
        description="Привязанная версия ОС (None, если обновление упало).",
    )
    busy_state: str = Field(description="Итоговое busy_state сервера (обычно free).")


class AcsSnapshotCreatedCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{id}/acs-snapshot-created — callback воркера.

    Воркер сообщает исход создания снимка (Clonezilla save-disk). В любом
    исходе снимает `busy_state=acs → free` — create не переписывает диск,
    сервер свободен независимо от результата.
    """

    os_version_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Версия каталога ОС, под которой создавался снимок.",
    )
    snapshot_name: str | None = Field(
        default=None, max_length=256,
        description="Имя созданного снимка в ACS, если операция дошла до этой стадии.",
    )
    succeeded: bool = Field(description="True — снимок создан; False — ACS вернул ошибку.")
    error: str | None = Field(
        default=None, max_length=2048,
        description="Текст ошибки ACS/reachability-таймаута, если succeeded=False.",
    )


class AcsSnapshotCreatedCallbackResponse(BaseModel):
    """Подтверждение записи acs-snapshot-created callback'а."""

    ok: bool = True
    busy_state: str = Field(
        description=(
            "Итоговое busy_state сервера — восстановлено из "
            "pre_acs_busy_snapshot (обычно free, но может быть busy/testing, "
            "если сервер был забронирован до запуска ACS)."
        )
    )


class AcsSnapshotRestoreDoneCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{id}/acs-snapshot-restore-done — callback воркера.

    Воркер сообщает исход восстановления снимка (Clonezilla restore-backup).
    `succeeded=True` — сервер снова доступен по SSH под старым/дефолтным
    образом: server_service резолвит bootstrap-пароль версии
    (`os_version_bootstrap_passwords`) и сам диспатчит `server.prepare`,
    `busy_state=acs` остаётся выставленным до его завершения. `succeeded=False`
    — восстановление не состоялось, сервер остался на прежнем диске,
    `busy_state` снимается сразу.
    """

    os_version_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Версия каталога ОС, к снимку которой восстанавливались.",
    )
    snapshot_name: str | None = Field(
        default=None, max_length=256,
        description="Имя восстановленного снимка в ACS.",
    )
    succeeded: bool = Field(description="True — сервер восстановлен и снова по SSH; False — restore упал.")
    error: str | None = Field(
        default=None, max_length=2048,
        description="Текст ошибки ACS/reachability-таймаута, если succeeded=False.",
    )


class AcsSnapshotRestoreDoneCallbackResponse(BaseModel):
    """Подтверждение записи acs-snapshot-restore-done callback'а."""

    ok: bool = True
    busy_state: str = Field(
        description=(
            "Итоговое busy_state сервера (acs — ждём auto-prepare; при "
            "succeeded=False восстановлено из pre_acs_busy_snapshot, обычно free)."
        )
    )
    prepare_task_id: str | None = Field(
        default=None,
        description="task_id авто-диспатченного server.prepare (только при succeeded=True).",
    )
