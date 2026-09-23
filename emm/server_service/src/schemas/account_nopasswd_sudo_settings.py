"""Pydantic-схемы NOPASSWD sudo для тестовых учёток (`/settings/account-nopasswd-sudo*`).

Два уровня доступа на одной таблице (`AccountNopasswdSudoSettings`):

* self-service — `department_admin`/`admin` service-роль своего отдела читает
  и правит ровно свою строку (`AccountNopasswdSudoSettingsResponse`/`Update`);
* оверсайт — `account_admin` видит и правит флаг любого отдела списком/батчем
  (`AccountNopasswdSudoSettingsItem`/`ListResponse`/`BatchUpdate`), тот же
  паттерн, что и `/settings/acs/departments`.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class AccountNopasswdSudoSettingsResponse(BaseModel):
    """Текущий флаг своего отдела (self-service)."""

    department_id: str = Field(description="ID отдела caller'а.")
    is_enabled: bool = Field(
        description=(
            "Класть ли NOPASSWD sudoers-правило sudo-аккаунтам (has_sudo=True) "
            "при provision/prepare на серверах и ВМ этого отдела."
        ),
    )
    updated_at: datetime | None = Field(
        default=None, description="Когда флаг менялся в последний раз. None — отдел ещё ни разу не настраивался.",
    )


class AccountNopasswdSudoSettingsUpdate(BaseModel):
    """Тело PUT /settings/account-nopasswd-sudo — своя строка целиком."""

    is_enabled: bool = Field(description="Новое значение флага для своего отдела.")


class AccountNopasswdSudoSettingsItem(BaseModel):
    """Одна строка списка отделов (оверсайт account_admin)."""

    department_id: str = Field(description="ID отдела (auth_service).")
    is_enabled: bool
    updated_at: datetime | None = None
    created_by: str | None = Field(default=None, description="Кто впервые включил/выключил отделу флаг.")


class AccountNopasswdSudoSettingsListResponse(BaseModel):
    """Ответ GET /settings/account-nopasswd-sudo/departments."""

    items: list[AccountNopasswdSudoSettingsItem]


class AccountNopasswdSudoSettingsBatchItem(BaseModel):
    """Один апдейт в батче PUT /settings/account-nopasswd-sudo/departments."""

    department_id: str = Field(description="ID отдела, которому меняем флаг.")
    is_enabled: bool = Field(description="Новое значение флага.")


class AccountNopasswdSudoSettingsBatchUpdate(BaseModel):
    """Тело PUT /settings/account-nopasswd-sudo/departments — один или несколько флагов за раз."""

    items: list[AccountNopasswdSudoSettingsBatchItem] = Field(min_length=1, max_length=200)
