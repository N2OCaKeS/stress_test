"""Схемы для internal-эндпоинтов, которые зовёт server_worker."""

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.core.password_policy import validate_password


class IpmiCredentialsResponse(BaseModel):
    """Ответ GET /internal/.../ipmi/credentials. Расшифрованные данные iDRAC/iLO/IPMI/Redfish."""

    controller_id: str = Field(description="ID контроллера в таблице ipmi_controllers (нужен worker'у для credentials_rotated callback'а).")
    kind: str = Field(description="Тип BMC: idrac / ilo / ipmi / redfish.")
    endpoint_url: str = Field(description="HTTPS URL Redfish API или IPMI host:port.")
    username: str = Field(description="Логин IPMI-аккаунта.")
    password: str = Field(description="Расшифрованный IPMI-пароль (plaintext, только worker'у).")


class AccountPasswordResponse(BaseModel):
    """Ответ GET /internal/.../accounts/{id}/password. Расшифрованный пароль OS-аккаунта."""

    login: str = Field(description="Login OS-аккаунта.")
    password: str = Field(description="Расшифрованный пароль (plaintext, только worker'у).")


class ManagementCredentialsResponse(BaseModel):
    """Ответ GET /internal/.../management/credentials. Per-server управляющие креды (#3)."""

    management_user: str | None = Field(
        description="Имя управляющего пользователя (dbos) на сервере. None — сервер ещё не prepared.",
    )
    ssh_private_key: str = Field(
        description="Расшифрованный приватный SSH-ключ управляющего пользователя (PEM, только worker'у).",
    )
    password: str = Field(
        description="Расшифрованный пароль управляющего пользователя (plaintext, только worker'у; sudo -S / console).",
    )


class ManagementCredsAppliedResponse(BaseModel):
    """Ответ applied-callback'а ротации управляющих кред."""

    ok: bool = True
    rotated_at: str = Field(description="ISO-8601 UTC момент подтверждения применения на боксе.")


class PasswordRotateRequest(BaseModel):
    """Тело POST /internal/.../accounts/{id}/password/rotate — новый пароль от worker'а."""

    password: str = Field(
        ...,
        min_length=8,
        description=(
            "Новый plaintext-пароль; server_service шифрует и сохраняет. "
            "Проверяется парольной политикой (минимум 8 символов, буква+цифра) — "
            "штатный `secrets.token_urlsafe(32)` worker'а проходит её сам, но "
            "кастомизированный отправитель защищён на приёмной стороне."
        ),
    )

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return validate_password(value)


class PasswordRotateResponse(BaseModel):
    """Ответ ротации. `rotated_at` — UTC ISO-8601 момент применения."""

    ok: bool = True
    rotated_at: str = Field(description="ISO-8601 timestamp в UTC, когда пароль был обновлён в БД.")


# ── Inventory callback ──────────────────────────────────────────────────────

class InventoryDiskItem(BaseModel):
    """Один диск в inventory-payload. Размер в гигабайтах (worker считает на стороне SSH-probe'а)."""

    name: str = Field(..., min_length=1, max_length=64, description="device_name (sda, nvme0n1).")
    size_gb: int = Field(..., ge=0, description="Размер диска в гигабайтах.")
    used_gb: int | None = Field(
        default=None, ge=0,
        description=(
            "Занято на диске в гигабайтах (сумма used всех его ФС из df). "
            "None — диск не смонтирован или df недоступен (back-compat со "
            "старым воркером, который поле не слал)."
        ),
    )
    used_percent: float | None = Field(
        default=None, ge=0, le=100,
        description="Процент занятости диска (used/size). None — если used неизвестен.",
    )
    model: str | None = Field(default=None, max_length=256, description="Модель диска.")
    serial: str | None = Field(default=None, max_length=128, description="Serial number.")
    device_path: str | None = Field(default=None, max_length=128, description="Полный путь устройства (/dev/sda).")
    mountpoints: list[str] = Field(
        default_factory=list,
        description="Точки монтирования поддерева диска, например /, /srv/ftp.",
    )
    is_system: bool = Field(default=False, description="Системный (с root /).")


class InventoryCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{id}/inventory — worker отдаёт hardware facts.

    CPU-поля плоские (`cpu_brand`/`cpu_model`/`cpu_cores`/`cpu_threads`/
    `cpu_frequency_ghz`) — пишутся прямо в строку `servers`, отдельной
    таблицы-каталога нет. `cpu_brand`/`cpu_model` могут быть `None`, если
    worker не смог распарсить lscpu.
    """

    hostname: str = Field(..., min_length=1, max_length=255, description="Hostname с пробинга.")
    kernel: str = Field(..., min_length=1, max_length=256, description="Версия ядра (uname -r).")
    cpu_brand: str | None = Field(default=None, max_length=64, description="Производитель CPU (Intel/AMD/MCST/...).")
    cpu_model: str | None = Field(default=None, max_length=256, description="Модель CPU (Model name из lscpu).")
    cpu_cores: int = Field(..., ge=1, description="Число физических ядер.")
    cpu_threads: int | None = Field(default=None, ge=1, description="Число потоков CPU (с учётом SMT/HT).")
    cpu_frequency_ghz: float | None = Field(default=None, ge=0, description="Базовая частота CPU в ГГц.")
    os_version: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[\w./()+,:\- ]+$",
        description=(
            "Версия ОС для lookup/записи в os_versions.name. Воркер отдаёт "
            "чистую версию ('1.8.1.6'), без имени дистрибутива и режима — режим "
            "едет отдельным полем os_security_mode."
        ),
    )
    os_security_mode: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^[A-Za-z]+$",
        description=(
            "Режим безопасности Astra латиницей: Smolensk / Orel / Voronezh. "
            "Per-server факт (пишется в servers.os_security_mode), в каталог "
            "версий НЕ идёт. Опционально — старый воркер поле не шлёт "
            "(back-compat), тогда существующее значение сервера не трогается."
        ),
    )
    ram_total_mb: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Объём ОЗУ в МБ (MemTotal из /proc/meminfo). Подчиняется модели "
            "warn-on-drift как CPU-поля: first-write сохраняется, расхождение с "
            "БД-истиной эмитит WARNING и не перетирается. Опционально — старый "
            "воркер поле не шлёт (back-compat)."
        ),
    )
    virtualization: bool | None = Field(
        default=None,
        description=(
            "Аппаратная виртуализация (KVM): True — есть /dev/kvm либо флаг "
            "vmx/svm в /proc/cpuinfo, False — нет. Пишется в "
            "servers.virtualization как авторитетный факт детекта (гейт кнопки "
            "«Подготовить как VMS-hub»). None — старый воркер поле не шлёт "
            "(back-compat), тогда значение сервера не трогается."
        ),
    )
    network_interfaces: list[str] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Активные сетевые интерфейсы бокса без lo (ip -o link show). "
            "Box-authoritative: непустой список перезаписывает хранимый. "
            "Пустой/отсутствует — существующий не трогается (back-compat)."
        ),
    )

    @field_validator("network_interfaces")
    @classmethod
    def _validate_network_interfaces(cls, value: list[str]) -> list[str]:
        # Имя интерфейса Linux — до 15 символов, тот же безопасный набор, что и
        # device_name дисков. Мусор из битого ip-output не должен раздувать
        # JSONB-колонку или лезть в UI как есть.
        pattern = re.compile(r"^[A-Za-z0-9._\-]+$")
        for name in value:
            if len(name) > 32 or not pattern.match(name):
                raise ValueError(
                    f"network interface '{name}' не соответствует ожидаемому шаблону"
                )
        return value
    repositories: list[str] = Field(
        default_factory=list,
        max_length=128,
        description=(
            "Активные репозитории ОС (из /etc/apt/sources.list). Снапшот версии "
            "ОС: непустой список перезаписывает хранимый в каталоге os_versions. "
            "Поле опционально — старый воркер его не шлёт (back-compat), тогда "
            "репозитории версии не трогаются."
        ),
    )
    disks: list[InventoryDiskItem] = Field(
        default_factory=list,
        max_length=128,
        description="Список дисков с probe'а (cap=128, hardware-разумный потолок).",
    )

    @field_validator("disks")
    @classmethod
    def _at_most_one_system_disk(cls, value: list[InventoryDiskItem]) -> list[InventoryDiskItem]:
        # На уровне БД инвариант держит partial unique index
        # (`ix_server_disks_system`), но без schema-проверки `_upsert_disks`
        # уронит 500 на IntegrityError при двух system-дисках в одном payload'е.
        system_count = sum(1 for d in value if d.is_system)
        if system_count > 1:
            raise ValueError(
                f"at most one disk may have is_system=True, got {system_count}"
            )
        return value

    @field_validator("repositories")
    @classmethod
    def _bound_repositories(cls, value: list[str]) -> list[str]:
        # sources.list-строки не бесконечны; ограничиваем длину элемента, чтобы
        # битый payload не раздул колонку каталога.
        for item in value:
            if len(item) > 2048:
                raise ValueError("repository entry too long (max 2048 chars)")
        return value


class InventoryCallbackResponse(BaseModel):
    """Ответ inventory-callback'а. Возвращаем что upsert'нули.

    `first_write_fields` — hardware-поля, заполненные впервые (в БД было пусто).
    `drift_fields` — поля, где факт бокса разошёлся с БД-истиной; они НЕ
    перетёрты, по ним эмитится WARNING `inventory.drift_detected`.
    """

    ok: bool = True
    os_version_id: str | None = Field(default=None, description="ID upsert'нутой OS-версии.")
    disks_upserted: int = Field(default=0, description="Сколько disk-записей upsert'нуто (INSERT + UPDATE).")
    first_write_fields: list[str] = Field(
        default_factory=list,
        description="Hardware-поля, сохранённые впервые (хранимое было NULL).",
    )
    drift_fields: list[str] = Field(
        default_factory=list,
        description="Поля с расхождением бокс↔БД (НЕ перетёрты; WARNING-аудит).",
    )


# ── OS-user inventory callback ──────────────────────────────────────────────

class InventoryUserItem(BaseModel):
    """Один OS-пользователь, найденный на сервере (getent passwd + доп.данные).

    Системные пользователи (UID < UID_MIN из /etc/login.defs) отфильтрованы
    воркером — сюда приходят только «человеческие» / прикладные аккаунты.
    """

    login: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._\-]+$",
        description="Имя пользователя (поле 1 из getent passwd).",
    )
    uid: int = Field(..., ge=0, description="UID пользователя.")
    shell: str | None = Field(default=None, max_length=64, description="Login shell.")
    home_dir: str | None = Field(default=None, max_length=256, description="Home-директория.")
    unix_groups: list[str] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Список групп (getent group). Cap=64 — реальный POSIX-аккаунт не "
            "состоит в десятках групп, ограничение защищает reconcile от "
            "вздутого payload'а."
        ),
    )

    @field_validator("unix_groups")
    @classmethod
    def _validate_unix_groups(cls, value: list[str]) -> list[str]:
        # POSIX group name: начинается с [a-z_], дальше [a-z0-9_-], общая длина
        # до 32 символов (login.defs default). Имена за пределами этого
        # шаблона — мусор из getent (битый UTF-8 / inline-CR) или попытка
        # инъекции в audit details.
        pattern = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
        for name in value:
            if len(name) > 32 or not pattern.match(name):
                raise ValueError(
                    f"unix_groups: '{name}' не соответствует POSIX group name pattern"
                )
        return value
    has_sudo: bool = Field(
        default=False, description="Состоит в sudo/admin-группе либо есть запись в sudoers."
    )


class UsersInventoryCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{id}/users/inventory — worker отдаёт
    список реальных OS-пользователей сервера."""

    users: list[InventoryUserItem] = Field(
        default_factory=list,
        max_length=1000,
        description=(
            "Найденные пользователи (без системных). Cap=1000 — защита от "
            "массивного payload'а, который раздул бы reconcile + N drift-emit'ов."
        ),
    )


class DriftItem(BaseModel):
    """Один drift-сигнал в `result_summary`.

    Зеркалит details emit'а `server_account.drift_detected`:

    * `unknown_login` — на боксе живёт юзер, которого в API нет (discovered
      больше НЕ создаётся, юзер уходит в `unknown_users`);
    * `attributes` — login совпадает, но атрибуты (has_sudo / unix_groups /
      shell / home_dir) разошлись с БД-истиной; `fields` перечисляет
      разошедшиеся поля;
    * `missing_on_box` — аккаунт привязан в API, но на боксе не найден.
    """

    login: str = Field(description="Login на боксе / в API.")
    drift_type: str = Field(description="unknown_login / attributes / missing_on_box.")
    fields: list[str] | None = Field(
        default=None,
        description="Для drift_type='attributes' — разошедшиеся поля.",
    )


class UsersInventoryResultSummary(BaseModel):
    """Сводка reconcile инвентаризации, возвращаемая worker'у вместе с
    обычными счётчиками. Worker может сохранить её в `tasks.result_payload`
    либо клиент агрегирует через `GET /servers/{id}/drift`."""

    total_users: int = Field(description="Сколько OS-пользователей пришло в payload (после фильтра системных).")
    created_discovered: int = Field(description="DEPRECATED: всегда 0 — авто-создание discovered убрано (см. unknown_users).")
    drifts: list[DriftItem] = Field(
        default_factory=list,
        description=(
            "Подробный список drift-сигналов. Не отрезается по cap — payload "
            "сам ограничен 1000 юзерами через `UsersInventoryCallbackRequest`."
        ),
    )


class AttrDiffValue(BaseModel):
    """Одно разошедшееся поле: что в БД (`expected`) и что на боксе (`found`).

    Значения отдаём как есть: `has_sudo` — bool, `unix_groups` — list[str],
    `shell` — str|None. Оператор сверяет и решает, принимать ли `found` в БД
    через `adopt_from_host`.
    """

    expected: Any = Field(description="Значение в БД (источник истины).")
    found: Any = Field(description="Значение, найденное на боксе.")


class AccountAttrDiff(BaseModel):
    """Расхождение атрибутов одного привязанного аккаунта с фактом на боксе.

    Только для существующих (не discovered в этом проходе) привязок, у которых
    `has_sudo`/`unix_groups`/`shell` разошлись с БД. `fields` — карта
    `имя_поля → {expected, found}`.
    """

    account_id: str = Field(description="ID аккаунта.")
    login: str = Field(description="OS-логин аккаунта.")
    fields: dict[str, AttrDiffValue] = Field(
        description="Разошедшиеся поля: has_sudo / unix_groups / shell → {expected, found}.",
    )


class UnknownUserItem(BaseModel):
    """Незнакомый OS-пользователь, найденный на боксе.

    Прошёл UID-фильтр воркера, есть на сервере, НЕ привязан ни к одному
    аккаунту и НЕ в ignore-list'е отдела. Reconcile больше НЕ заводит для
    него discovered-аккаунт автоматически — отдаёт сюда, чтобы воркер положил
    в `task.result`, а оператор решил: импортировать (создать аккаунт) либо
    заигнорить.
    """

    login: str = Field(description="OS-логин на боксе.")
    uid: int = Field(description="UID пользователя.")
    has_sudo: bool = Field(description="Состоит в sudo/admin-группе либо есть запись в sudoers.")
    unix_groups: list[str] = Field(default_factory=list, description="Список Unix-групп с бокса.")
    shell: str | None = Field(default=None, description="Login shell.")


class UnlinkedExistingCandidate(BaseModel):
    """Один аккаунт-кандидат на связку для логина из `unlinked_existing`.

    Login на ServerAccount не уникален в рамках department'а, поэтому на один
    обнаруженный логин может прийтись несколько кандидатов — оператор выберет
    нужный в UI.
    """

    account_id: str = Field(description="ID существующего аккаунта-кандидата.")
    department_id: str = Field(description="Department аккаунта (= department сервера).")
    source: str = Field(description="Происхождение аккаунта: managed / discovered.")


class UnlinkedExistingItem(BaseModel):
    """OS-логин, найденный на боксе, под который в отделе УЖЕ есть аккаунт, но
    он НЕ привязан к этому серверу.

    Reconcile сюда ничего не линкует и не создаёт — отдаёт оператору, чтобы UI
    показал модалку «связать существующий аккаунт с сервером». От `unknown_users`
    отличается тем, что аккаунт-кандидат уже существует.
    """

    login: str = Field(description="OS-логин на боксе.")
    uid: int = Field(description="UID пользователя.")
    candidates: list[UnlinkedExistingCandidate] = Field(
        description="Существующие аккаунты отдела с этим login'ом (≥1).",
    )


class UsersInventoryCallbackResponse(BaseModel):
    """Сводка reconcile инвентаризации пользователей."""

    ok: bool = True
    created: int = Field(default=0, description="DEPRECATED: всегда 0 — авто-создание discovered убрано. Незнакомые юзеры теперь в `unknown_users`.")
    present: int = Field(default=0, description="Сколько существующих аккаунтов подтверждено на боксе (present_on_server=True).")
    drifted: int = Field(default=0, description="Сколько drift-сигналов поднято: расхождение атрибутов + привязки, отсутствующие на боксе.")
    diffs: list[AccountAttrDiff] = Field(
        default_factory=list,
        description=(
            "Структурированный per-account diff с значениями (expected/found) — "
            "только для существующих привязанных аккаунтов с расхождением "
            "атрибутов. Данные для ручного ревью: БД не перетирается. Worker "
            "кладёт их в `task.result`, UI показывает оператору."
        ),
    )
    unknown_users: list[UnknownUserItem] = Field(
        default_factory=list,
        description=(
            "OS-пользователи, найденные на боксе, но НЕ привязанные ни к "
            "одному аккаунту и НЕ в ignore-list'е отдела. Reconcile их больше "
            "НЕ заводит автоматически — оператор решает по карточке сервера: "
            "импортировать (создать аккаунт) или заигнорить. Worker кладёт "
            "список в `task.result`."
        ),
    )
    unlinked_existing: list[UnlinkedExistingItem] = Field(
        default_factory=list,
        description=(
            "OS-логины с бокса, под которые в отделе УЖЕ есть аккаунт, но он "
            "НЕ привязан к этому серверу. Reconcile НЕ создаёт и НЕ линкует "
            "автоматически — отдаёт оператору, UI предлагает связать "
            "существующий аккаунт. Не дрейфит как unknown_login."
        ),
    )
    result_summary: UsersInventoryResultSummary | None = Field(
        default=None,
        description=(
            "Подробная сводка — список drift'ов с типом и затронутыми полями. "
            "Worker кладёт её в `tasks.result_payload`, либо клиент строит "
            "drift-отчёт через `GET /servers/{id}/drift`."
        ),
    )


# ── OS-user provision callback ──────────────────────────────────────────────

class ProvisionStatusRequest(BaseModel):
    """Тело POST /internal/servers/{id}/accounts/{aid}/provision_status.

    Worker сообщает результат useradd/usermod/userdel на боксе. server_service
    обновляет `present_on_server` на связке (аккаунт ↔ сервер): provision/update
    → True, deprovision → False. `present` несёт целевое состояние явно, чтобы
    приёмная сторона не выводила его из operation (контракт остаётся явным).
    """

    operation: str = Field(
        ...,
        pattern=r"^(provision|update|deprovision)$",
        description="provision (useradd) / update (usermod) / deprovision (userdel).",
    )
    present: bool = Field(
        ...,
        description="Целевое состояние присутствия после операции: True — пользователь на боксе есть, False — удалён.",
    )


class ProvisionStatusResponse(BaseModel):
    """Подтверждение записи provision-callback'а."""

    ok: bool = True
    present_on_server: bool = Field(description="Записанное в связке состояние присутствия.")


# ── Power-state writeback callback ──────────────────────────────────────────

class PowerStateCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{id}/power-state.

    Воркер шлёт результат живой пробы `power.status` одним callback'ом с тремя
    независимыми сигналами доступности: ping, ssh и питание по BMC (ipmi).
    Каждый из них опционален — приходит только если проба его измеряла.
    server_service пишет присланные сигналы в свои тройки колонок
    (`ping_*`/`ssh_*`/`ipmi_*`) с моментом приёма (UTC).

    `power_state` + `source` — legacy-тройка сводного состояния питания. Воркер
    теперь шлёт callback ВСЕГДА, даже когда `power_state="unknown"`; приёмная
    сторона на unknown не перетирает ранее закэшированные legacy-значения, а
    новые ping/ssh/ipmi сигналы записывает в любом случае.

    Idempotent best-effort: повторный callback просто перезаписывает кэш.
    """

    power_state: Literal["on", "off", "unknown"] = Field(
        ...,
        description="Сводное состояние питания (legacy): on / off / unknown.",
    )
    source: Literal["bmc", "ping", "ssh"] = Field(
        ...,
        description="Чем получено сводное power_state (legacy): bmc (Redfish/ipmitool) / ping / ssh.",
    )
    ping_reachable: bool | None = Field(
        default=None,
        description="Ответил ли сервер на ping. None — ping в этой пробе не мерился.",
    )
    ping_latency_ms: float | None = Field(
        default=None,
        description="RTT ping в миллисекундах. None — недоступен либо не мерился.",
    )
    ssh_reachable: bool | None = Field(
        default=None,
        description="Доступен ли SSH-порт. None — ssh в этой пробе не мерился.",
    )
    ssh_latency_ms: float | None = Field(
        default=None,
        description="Задержка SSH-пробы в миллисекундах. None — недоступен либо не мерился.",
    )
    ipmi_power_state: Literal["on", "off", "unknown"] | None = Field(
        default=None,
        description="Питание по BMC/IPMI: on / off / unknown. None — BMC в этой пробе не опрашивался.",
    )


class PowerStateCallbackResponse(BaseModel):
    """Подтверждение записи power-state callback'а."""

    ok: bool = True
    power_state: str = Field(description="Записанное в кэш состояние питания.")
    checked_at: str = Field(description="ISO-8601 UTC момент приёма результата пробы.")


class AutoInventorySweepResponse(BaseModel):
    """Сводка планового авто-inventory прогона (POST /internal/servers/auto-inventory-sweep)."""

    ok: bool = True
    total_managed: int = Field(description="Всего подготовленных (is_managed) серверов.")
    processed: int = Field(description="Серверов, по которым прошёл фан-аут (после cap'а).")
    dispatched_tasks: int = Field(description="Сколько задач (inventory.sync + power.status) реально поставлено.")
    truncated: int = Field(description="Сколько серверов отрезано cap'ом AUTO_INVENTORY_FANOUT_MAX.")
    stuck_updating_recovered: int = Field(
        default=0,
        description=(
            "Сколько серверов освобождено из залипшего busy_state=updating по "
            "TTL перед фан-аутом (astra-updated callback не пришёл)."
        ),
    )


class PowerSweepResponse(BaseModel):
    """Сводка частого power-прогона (POST /internal/servers/power-sweep)."""

    ok: bool = True
    total_servers: int = Field(description="Всего не-списанных серверов платформы.")
    processed: int = Field(description="Серверов, по которым прошёл фан-аут (после cap'а).")
    dispatched_tasks: int = Field(description="Сколько power.status-задач реально поставлено.")
    truncated: int = Field(description="Сколько серверов отрезано cap'ом AUTO_INVENTORY_FANOUT_MAX.")


class VmStatusSweepResponse(BaseModel):
    """Сводка частого статус-прогона ВМ (POST /internal/vms/status-sweep)."""

    ok: bool = True
    total_vms: int = Field(description="Всего активных ВМ платформы.")
    processed: int = Field(description="ВМ, по которым прошёл фан-аут (после cap'а, с живым hub'ом).")
    dispatched_tasks: int = Field(description="Сколько vm.status-задач реально поставлено.")
    truncated: int = Field(description="Сколько ВМ отрезано cap'ом AUTO_INVENTORY_FANOUT_MAX.")


class VmCreateReconcileResponse(BaseModel):
    """Сводка reconcile'а упавших vm.create (POST /internal/vms/reconcile-failed-creates)."""

    ok: bool = True
    checked: int = Field(description="ВМ в busy_state=creating, проверенных в этом прогоне.")
    deleted: int = Field(description="ВМ, удалённых из-за терминально-провальной vm.create-задачи.")
    skipped: int = Field(description="ВМ, не тронутых (задача ещё жива / успешна / отсутствует / ошибка обработки).")


# ── IPMI credentials_rotated callback ───────────────────────────────────────

class IpmiCredentialsRotatedRequest(BaseModel):
    """Тело POST /internal/ipmi-controllers/{id}/credentials_rotated.

    Worker присылает plaintext-пароль по TLS внутри кластера; шифрует
    приёмная сторона через `secrets_service.encrypt()` — у worker'а нет
    `SERVER_ENCRYPTION_KEY`.

    Контракт verify-then-storage: storage обновляется ТОЛЬКО если worker
    предварительно подтвердил, что новый пароль реально работает на BMC
    (BMC test-call после apply). Подтверждение — поле `verified_at`,
    timestamp успешного test-call'а. Без `verified_at` или со «старым»
    `verified_at` приёмник отбивает 400 `BMC_VERIFY_REQUIRED` — иначе
    при сбое apply→verify мы записали бы ciphertext, которым нельзя
    залогиниться, и out-of-band доступ был бы потерян до ручной починки.

    Окно свежести `verified_at` — `IPMI_VERIFY_MAX_AGE_SECONDS` (default
    60s). 60s достаточно для round-trip apply→verify→post, при этом не
    даёт реиспользовать verify-результат поверх давнего successful test.
    """

    new_password: str = Field(
        ...,
        min_length=8,
        description=(
            "Новый plaintext-пароль (через TLS внутри cluster'а). Проверяется "
            "парольной политикой (минимум 8 символов, буква+цифра)."
        ),
    )
    rotated_at: datetime = Field(description="ISO-8601 timestamp UTC момента ротации.")
    verified_at: datetime = Field(
        ...,
        description=(
            "ISO-8601 timestamp UTC момента успешного BMC test-call'а после "
            "apply. Обязателен; должен быть свежее `IPMI_VERIFY_MAX_AGE_SECONDS` "
            "(см. config). Без него — 400 BMC_VERIFY_REQUIRED."
        ),
    )

    @field_validator("new_password")
    @classmethod
    def _validate_new_password(cls, value: str) -> str:
        return validate_password(value)

    @field_validator("verified_at", "rotated_at")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        # Без tzinfo трактуем как UTC — internal-канал работает в UTC, а
        # клиент-libsы (стандарт.lib `datetime.utcnow()`) исторически отдают
        # naive значения. Внутри service'а сравниваем уже tz-aware.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class IpmiCredentialsRotatedResponse(BaseModel):
    """Подтверждение записи rotate-callback'а."""

    ok: bool = True
    rotated_at: str = Field(description="Сохранённый timestamp ротации (ISO-8601 UTC).")



# ── Бронь сервера от имени сервиса (s2s, X-Service-Identity) ────────────────

# Состояния, в которые сервис вправе перевести взятый им сервер. `free` сюда
# не входит (снятие брони — отдельный release-эндпоинт), `updating` тоже:
# это системная блокировка astra-update со своим владельцем-callback'ом.
ServiceBusyState = Literal["busy", "testing", "acs"]


class ServiceAcquireRequest(BaseModel):
    """Тело POST /internal/servers/{id}/acquire-for-service."""

    busy_state: ServiceBusyState = Field(
        default="acs",
        description=(
            "Стадия, в которую переводится сервер при захвате. Дефолт `acs` — "
            "цикл testing_service начинается с подготовки стенда."
        ),
    )
    busy_note: str | None = Field(
        default=None,
        max_length=512,
        description="Человекочитаемая метка причины занятости (формат — на стороне вызывающего сервиса).",
    )
    requested_by_department_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Отдел, от имени которого сервис берёт стенд. Необязателен: у "
            "сервисного каллера своего отдела нет, привязку стенда к отделу "
            "он ведёт у себя. Если прислан — сверяется с `server.department_id`, "
            "несовпадение маскируется под 404 SERVER_NOT_FOUND (как "
            "`X-Target-Department-Id` у worker-callback'ов)."
        ),
    )


class ServiceBusyStatusRequest(BaseModel):
    """Тело POST /internal/servers/{id}/service-status — смена стадии внутри брони."""

    busy_state: ServiceBusyState = Field(
        description="Новая стадия уже существующей брони (например `acs` → `testing`).",
    )
    busy_note: str | None = Field(
        default=None,
        max_length=512,
        description="Новая метка. None оставляет прежнюю — заметка не сбрасывается вместе со сменой стадии.",
    )


class ServiceReservationResponse(BaseModel):
    """Состояние брони после acquire / release / смены стадии."""

    server_id: str = Field(description="ID сервера.")
    busy_state: str = Field(description="Итоговое состояние занятости.")
    busy_actor_type: str = Field(description="user / service — кто держит бронь.")
    busy_service_name: str | None = Field(
        default=None,
        description="Имя сервиса-держателя (None после release).",
    )
    busy_note: str | None = Field(default=None, description="Метка причины занятости.")
    busy_since: datetime | None = Field(
        default=None,
        description="С какого момента держится бронь (UTC). Смена стадии его не двигает.",
    )


class ServerConnectionInfoResponse(BaseModel):
    """Ответ GET /internal/servers/{id}/connection-info.

    Единственный внутренний потребитель — `testing_worker`: у него нет
    пользовательского bearer'а для pass-through `GET /servers/{id}`
    (`_ensure_visible` гейтит его по department_id держателя токена), а
    прошивать IP стенда в `test_stands` намеренно не стали (§4 плана
    миграции — "надстройка без дублирования"). Отдаёт только то, что нужно
    для SSH-подключения, не полную карточку сервера.
    """

    server_id: str = Field(description="ID сервера.")
    host: str = Field(description="`Server.ip_address` — подключаться по IP, не по hostname.")


class ServerBatchStatusRequest(BaseModel):
    """Тело POST /internal/servers/batch-status."""

    server_ids: list[str] = Field(
        min_length=1,
        max_length=500,
        description="ID серверов одним запросом — не устраивать N вызовов на N стендов пула.",
    )


class ServerStatusItem(BaseModel):
    """Один сервер в ответе batch-status — ping + busy на текущий момент."""

    server_id: str = Field(description="ID сервера, как в запросе.")
    found: bool = Field(description="False — сервера с этим id не существует (не 404 на весь батч).")
    busy_state: str | None = Field(default=None, description="`Server.busy_state`. None, если сервер не найден.")
    busy_service_name: str | None = Field(default=None, description="Имя сервиса-держателя брони, если есть.")
    ping_reachable: bool | None = Field(default=None, description="Последний живой сигнал ping. None — проб ещё не было.")
    ping_checked_at: datetime | None = Field(default=None, description="Момент последнего ping-замера (UTC).")


class ServerBatchStatusResponse(BaseModel):
    """Ответ POST /internal/servers/batch-status."""

    servers: list[ServerStatusItem] = Field(description="Один элемент на каждый запрошенный server_id, в любом порядке.")
