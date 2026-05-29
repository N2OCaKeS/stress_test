"""Схемы для internal-эндпоинтов, которые зовёт server_worker."""

from datetime import datetime

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
        default_factory=list, description="Список групп (getent group)."
    )
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


class UsersInventoryCallbackResponse(BaseModel):
    """Сводка reconcile инвентаризации пользователей."""

    ok: bool = True
    created: int = Field(default=0, description="Сколько discovered-аккаунтов заведено (каждый — drift-сигнал).")
    present: int = Field(default=0, description="Сколько существующих аккаунтов подтверждено на боксе (present_on_server=True).")
    drifted: int = Field(default=0, description="Сколько drift-сигналов поднято: расхождение атрибутов + привязки, отсутствующие на боксе.")


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
    `SERVER_ENCRYPTION_KEY`. Этот контракт обязан выполняться до того,
    как worker применит новый пароль на BMC (storage-first ordering):
    иначе при падении worker'а между BMC-apply и storage-save доступ к
    iDRAC потерян безвозвратно.
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

    @field_validator("new_password")
    @classmethod
    def _validate_new_password(cls, value: str) -> str:
        return validate_password(value)


class IpmiCredentialsRotatedResponse(BaseModel):
    """Подтверждение записи rotate-callback'а."""

    ok: bool = True
    rotated_at: str = Field(description="Сохранённый timestamp ротации (ISO-8601 UTC).")


