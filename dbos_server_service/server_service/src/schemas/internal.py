"""Схемы для internal-эндпоинтов, которые зовёт server_worker."""

from datetime import datetime

from pydantic import BaseModel, Field


class IpmiCredentialsResponse(BaseModel):
    """Ответ GET /internal/.../ipmi/credentials. Расшифрованные данные iDRAC/iLO/IPMI/Redfish."""

    controller_id: str = Field(description="ID контроллера в таблице ipmi_controllers (нужен worker'у для credentials_rotated callback'а).")
    kind: str = Field(description="Тип BMC: idrac / ilo / ipmi / redfish.")
    bmc_vendor: str = Field(
        default="ipmi_generic",
        description=(
            "Vendor BMC (idrac / ilo / ipmi_generic) — нужен worker'у для "
            "выбора корректных Redfish-paths (/Managers/<vendor-id>)."
        ),
    )
    endpoint_url: str = Field(description="HTTPS URL Redfish API или IPMI host:port.")
    username: str = Field(description="Логин IPMI-аккаунта.")
    password: str = Field(description="Расшифрованный IPMI-пароль (plaintext, только worker'у).")


class AccountPasswordResponse(BaseModel):
    """Ответ GET /internal/.../accounts/{id}/password. Расшифрованный пароль OS-аккаунта."""

    login: str = Field(description="Login OS-аккаунта.")
    password: str = Field(description="Расшифрованный пароль (plaintext, только worker'у).")


class PasswordRotateRequest(BaseModel):
    """Тело POST /internal/.../accounts/{id}/password/rotate — новый пароль от worker'а."""

    password: str = Field(..., min_length=1, description="Новый plaintext-пароль; server_service шифрует и сохраняет.")


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
    os_version: str = Field(..., min_length=1, max_length=128, description="OS-версия для lookup в os_versions.name.")
    disks: list[InventoryDiskItem] = Field(default_factory=list, description="Список дисков с probe'а.")
    lspci: str | None = Field(default=None, description="Сырой вывод lspci (опционален, под будущий debug).")


class InventoryCallbackResponse(BaseModel):
    """Ответ inventory-callback'а. Возвращаем что upsert'нули."""

    ok: bool = True
    os_version_id: str | None = Field(default=None, description="ID upsert'нутой OS-версии.")
    disks_upserted: int = Field(default=0, description="Сколько disk-записей upsert'нуто (INSERT + UPDATE).")


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
        ..., min_length=1,
        description="Новый plaintext-пароль (через TLS внутри cluster'а).",
    )
    rotated_at: datetime = Field(description="ISO-8601 timestamp UTC момента ротации.")


class IpmiCredentialsRotatedResponse(BaseModel):
    """Подтверждение записи rotate-callback'а."""

    ok: bool = True
    rotated_at: str = Field(description="Сохранённый timestamp ротации (ISO-8601 UTC).")


