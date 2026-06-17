"""Схемы для internal-эндпоинтов, которые зовёт server_worker."""

import re
from datetime import datetime, timezone
from typing import Any

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
    model: str | None = Field(default=None, max_length=256, description="Модель диска.")
    serial: str | None = Field(default=None, max_length=128, description="Serial number.")
    device_path: str | None = Field(default=None, max_length=128, description="Полный путь устройства (/dev/sda).")
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
        pattern=r"^[A-Za-z0-9._\- ]+$",
        description="OS-версия для lookup в os_versions.name.",
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


class InventoryCallbackResponse(BaseModel):
    """Ответ inventory-callback'а. Возвращаем что upsert'нули."""

    ok: bool = True
    os_version_id: str | None = Field(default=None, description="ID upsert'нутой OS-версии.")
    disks_upserted: int = Field(default=0, description="Сколько disk-записей upsert'нуто (INSERT + UPDATE).")


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


