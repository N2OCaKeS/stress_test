"""Pydantic-схемы для эндпоинтов /boxes.

Бокс — пер-департамент каталожная запись образа-заготовки для создания ВМ.
Пароль предустановленного пользователя принимаем на write как `base_user_password_b64`
(`base64.b64encode(plaintext)`) и в ответ не возвращаем; держателю action
`view_password` тот же GET доносит его обратно в `base_user_password_b64`.

Парольную политику к пользователю образа НЕ применяем: креды зашиты в артефакт
(нередко это тривиальные `u`/`1`), и наша задача — сохранить их как есть, а не
навязать сложность. Проверяется только корректность base64.
"""

from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.b64 import decode_b64

# Схемы URL, которые каталог принимает как источник скачивания бокса. Само
# скачивание/импорт — отдельная задача; здесь только валидируем метаданные.
_ALLOWED_URL_SCHEMES = {"http", "https", "ftp", "ftps", "smb", "cifs", "nfs"}


def _validate_download_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise ValueError(
            "download_url: недопустимая схема "
            f"(ожидается одна из {sorted(_ALLOWED_URL_SCHEMES)})"
        )
    if not parsed.netloc:
        raise ValueError("download_url: не указан хост")
    return value


class BoxCreate(BaseModel):
    """Тело POST /boxes — завести бокс-заготовку в своём отделе."""

    model_config = ConfigDict(extra="forbid")

    department_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Отдел-владелец бокса. Должен совпадать с отделом вызывающего.",
    )
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="Имя бокса. Уникально в пределах отдела.",
    )
    format: str = Field(
        ..., min_length=1, max_length=32,
        description="Формат артефакта: tar / qcow / qcow2 / raw / … (набор открытый).",
    )
    download_url: str | None = Field(
        default=None, max_length=1024,
        description=(
            "Источник скачивания (https/ftp/smb/http/…). Только метаданные — "
            "сам download/import идёт отдельной операцией. Схема валидируется."
        ),
    )
    base_user_login: str | None = Field(
        default=None, max_length=128,
        description="Логин предустановленного в образе пользователя.",
    )
    base_user_password_b64: str | None = Field(
        default=None, max_length=1024,
        description=(
            "Пароль предустановленного пользователя в base64 "
            "(`base64.b64encode(plaintext)`). Декодируется на приёме и шифруется "
            "через secrets_service ДО записи; в ответе не возвращается. "
            "Парольная политика к образным кредам не применяется. Битый base64 → 422."
        ),
    )
    os_versions: list[str] = Field(
        default_factory=list, max_length=64,
        description="Версии ОС, лежащие на диске образа изначально.",
    )
    initial_snapshots: list[str] = Field(
        default_factory=list, max_length=64,
        description="Имена снимков, присутствующих на диске образа изначально.",
    )

    @field_validator("download_url")
    @classmethod
    def _check_download_url(cls, value: str | None) -> str | None:
        return _validate_download_url(value)

    @field_validator("base_user_password_b64")
    @classmethod
    def _check_password_b64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Только корректность base64 — политику к образным кредам не применяем.
        decode_b64(value, "base_user_password_b64")
        return value

    def base_user_password(self) -> str | None:
        """Раскодированный plaintext пароля образного пользователя (или None)."""
        if self.base_user_password_b64 is None:
            return None
        return decode_b64(self.base_user_password_b64, "base_user_password_b64")


class BoxUpdate(BaseModel):
    """Тело PATCH /boxes/{id}. Все поля опциональны.

    `base_user_password_b64` меняет/задаёт пароль образного пользователя
    (шифруется тем же путём, что на create). `department_id` не редактируется —
    бокс не переносится между отделами.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    format: str | None = Field(default=None, min_length=1, max_length=32)
    download_url: str | None = Field(default=None, max_length=1024)
    base_user_login: str | None = Field(default=None, max_length=128)
    base_user_password_b64: str | None = Field(default=None, max_length=1024)
    os_versions: list[str] | None = Field(default=None, max_length=64)
    initial_snapshots: list[str] | None = Field(default=None, max_length=64)

    @field_validator("download_url")
    @classmethod
    def _check_download_url(cls, value: str | None) -> str | None:
        return _validate_download_url(value)

    @field_validator("base_user_password_b64")
    @classmethod
    def _check_password_b64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        decode_b64(value, "base_user_password_b64")
        return value

    def base_user_password(self) -> str | None:
        """Раскодированный plaintext нового пароля образного пользователя (или None)."""
        if self.base_user_password_b64 is None:
            return None
        return decode_b64(self.base_user_password_b64, "base_user_password_b64")


class BoxResponse(BaseModel):
    """Карточка бокса в ответе.

    `base_user_password_b64` заполняется только когда вызывающий держит action
    `view_password` — тогда это base64(plaintext). Иначе `None`. Сырого
    `base_user_password_encrypted` в ответе нет никогда.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Box ID (prefix box_).")
    department_id: str = Field(description="Отдел-владелец.")
    name: str = Field(description="Имя бокса.")
    format: str = Field(description="Формат артефакта.")
    download_url: str | None = Field(default=None, description="Источник скачивания.")
    base_user_login: str | None = Field(
        default=None, description="Логин предустановленного пользователя."
    )
    base_user_password_b64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded plaintext пароля образного пользователя. Присутствует "
            "только у держателя action `view_password`; иначе `null`."
        ),
    )
    os_versions: list[str] = Field(
        default_factory=list, description="Версии ОС на диске образа."
    )
    initial_snapshots: list[str] = Field(
        default_factory=list, description="Снимки на диске образа."
    )
    created_at: datetime = Field(description="Когда бокс создан.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
    created_by: str | None = Field(default=None, description="Кто создал бокс.")
