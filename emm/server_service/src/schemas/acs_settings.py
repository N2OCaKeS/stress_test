"""Pydantic-схемы настроек доступа к ACS (`/settings/acs`, `/settings/acs/departments`).

Платформенный singleton под `account_admin` + per-department opt-in таблица.
Пароль clonezilla-сервера никогда не возвращается в открытом виде — только
факт "задан/не задан" (`password_is_set`); обновление идёт write-only полем
в PUT.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class AcsSettingsResponse(BaseModel):
    """Текущие настройки доступа к ACS."""

    enabled: bool = Field(description="Общий кил-свитч снимков ACS на всю платформу.")
    acs_url: str | None = Field(default=None, description="Базовый URL ACS.")
    password_is_set: bool = Field(description="Задан ли пароль clonezilla-сервера. Само значение не отдаётся.")


class AcsSettingsUpdate(BaseModel):
    """Тело PUT — частичное обновление настроек ACS.

    Любое поле можно опустить — тогда текущее значение сохраняется.
    `acs_password` — plaintext на вход, шифруется на сервисном слое перед
    сохранением; пустое/отсутствующее значение не трогает уже сохранённый
    пароль. Чтобы явно стереть пароль (например, при смене clonezilla-сервера
    без немедленной замены креда) — `clear_password=True`.
    """

    enabled: bool | None = Field(default=None, description="Включить/выключить снимки ACS.")
    acs_url: str | None = Field(default=None, max_length=512, description="Базовый URL ACS.")
    acs_password: str | None = Field(
        default=None,
        description="Новый пароль clonezilla-сервера (plaintext). Пусто — не менять текущий.",
    )
    clear_password: bool = Field(
        default=False,
        description="Явно стереть сохранённый пароль (игнорируется, если одновременно передан `acs_password`).",
    )


class AcsDepartmentAccessItem(BaseModel):
    """Одна строка списка отделов с флагом доступа к снимкам ACS."""

    department_id: str = Field(description="ID отдела (auth_service).")
    is_enabled: bool = Field(description="Включён ли отделу доступ к снимкам ACS.")
    updated_at: datetime | None = Field(
        default=None, description="Когда флаг менялся в последний раз. None — отдел ещё ни разу не настраивался."
    )
    created_by: str | None = Field(default=None, description="Кто впервые включил/выключил отделу доступ.")


class AcsDepartmentAccessListResponse(BaseModel):
    """Ответ GET /settings/acs/departments."""

    items: list[AcsDepartmentAccessItem]


class AcsDepartmentAccessUpdateItem(BaseModel):
    """Один апдейт в батче PUT /settings/acs/departments."""

    department_id: str = Field(description="ID отдела, которому меняем флаг.")
    is_enabled: bool = Field(description="Новое значение флага доступа.")


class AcsDepartmentAccessUpdate(BaseModel):
    """Тело PUT /settings/acs/departments — один или несколько флагов за раз."""

    items: list[AcsDepartmentAccessUpdateItem] = Field(min_length=1, max_length=200)


class AcsInternalSettingsResponse(BaseModel):
    """Ответ GET /internal/settings/acs — для server_worker."""

    acs_url: str = Field(description="Базовый URL ACS.")
    acs_password: str = Field(description="Расшифрованный пароль clonezilla-сервера (plaintext, только worker'у).")
