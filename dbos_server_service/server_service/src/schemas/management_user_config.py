"""Pydantic-схемы конфига управляющей учётки (`/management-user-config`).

Платформенный singleton под `account_admin`. Конфиг описывает имя управляющего
пользователя и пер-режимные настройки bootstrap'а для четырёх режимов создания
учётки.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.constants import ManagementMode

# Разумные потолки, чтобы PUT не превращался в DoS на JSONB.
_MAX_GROUPS = 64
_MAX_GROUP_LEN = 64
_MAX_COMMANDS = 128
_MAX_COMMAND_LEN = 4096

# Unix-логин: начинается с буквы/подчёркивания, дальше буквы/цифры/`_`/`-`,
# опциональный завершающий `$`. До 32 символов (NAME_MAX в useradd).
_LOGIN_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}\$?$")

# Имя unix-группы — те же правила, что у логина, без trailing `$`.
_GROUP_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def _validate_login(value: str) -> str:
    """Логин управляющей учётки — валидный unix-username в нижнем регистре."""
    login = value.strip()
    if not _LOGIN_RE.match(login):
        raise ValueError(
            "login must be a valid lowercase unix username "
            "(letters/digits/_/-, starting with a letter or underscore, max 32)"
        )
    return login


def _validate_groups(value: list[str]) -> list[str]:
    """Доп-группы — непустые валидные имена unix-групп, в лимитах."""
    if len(value) > _MAX_GROUPS:
        raise ValueError(f"too many groups (max {_MAX_GROUPS})")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("group must be a string")
        name = item.strip()
        if not name:
            raise ValueError("group must be a non-empty string")
        if len(name) > _MAX_GROUP_LEN:
            raise ValueError(f"group name too long (max {_MAX_GROUP_LEN})")
        if not _GROUP_RE.match(name):
            raise ValueError(f"group must be a valid unix group name: {item!r}")
        cleaned.append(name)
    return cleaned


def _validate_commands(value: list[str]) -> list[str]:
    """Bootstrap-команды — непустые строки в разумных лимитах.

    Намеренно не парсим shell: команды выполняет worker под управляющим
    пользователем на боксе, синтаксис на его стороне. Здесь только защита от
    пустых/гигантских строк.
    """
    if len(value) > _MAX_COMMANDS:
        raise ValueError(f"too many commands (max {_MAX_COMMANDS})")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("command must be a string")
        cmd = item.strip()
        if not cmd:
            raise ValueError("command must be a non-empty string")
        if len(cmd) > _MAX_COMMAND_LEN:
            raise ValueError(f"command too long (max {_MAX_COMMAND_LEN})")
        cleaned.append(cmd)
    return cleaned


class ManagementModeConfig(BaseModel):
    """Настройки bootstrap'а управляющей учётки для одного режима."""

    model_config = ConfigDict(from_attributes=True)

    groups: list[str] = Field(
        default_factory=list,
        description="Дополнительные unix-группы управляющей учётки в этом режиме.",
    )
    extra_create_commands: list[str] = Field(
        default_factory=list,
        description=(
            "Shell-команды, прогоняемые при заведении учётки в этом режиме "
            "(например выставление уровней целостности для Смоленска)."
        ),
    )

    @field_validator("groups")
    @classmethod
    def _check_groups(cls, value: list[str]) -> list[str]:
        return _validate_groups(value)

    @field_validator("extra_create_commands")
    @classmethod
    def _check_commands(cls, value: list[str]) -> list[str]:
        return _validate_commands(value)


class ManagementUserConfigUpdate(BaseModel):
    """Тело PUT — полная замена конфига.

    `login` опционален: не прислали — оставляем текущий. `modes` — частичный
    словарь по режимам; присланные режимы заменяются целиком, неприсланные
    остаются как были. Ключи `modes` валидируются против `ManagementMode`.
    """

    login: str | None = Field(
        default=None,
        max_length=32,
        description="Имя управляющей учётки (валидный unix-логин). Не прислан — без изменений.",
    )
    modes: dict[ManagementMode, ManagementModeConfig] | None = Field(
        default=None,
        description="Пер-режимные настройки. Присланные режимы заменяются целиком.",
    )

    @field_validator("login")
    @classmethod
    def _check_login(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_login(value)


class ManagementUserConfigResponse(BaseModel):
    """Текущий конфиг управляющей учётки со всеми режимами."""

    login: str = Field(description="Имя управляющей учётки.")
    modes: dict[ManagementMode, ManagementModeConfig] = Field(
        description="Настройки на каждый из четырёх режимов создания учётки.",
    )
    login_changed: bool = Field(
        default=False,
        description=(
            "True, если этот ответ — результат PUT, сменившего `login`. "
            "Сигнал для будущей фазы: имя управляющей учётки изменилось и "
            "требует cutover-фан-аута на серверах. Сам фан-аут здесь не делается."
        ),
    )
    previous_login: str | None = Field(
        default=None,
        description="Прежнее имя управляющей учётки до PUT (только когда login_changed=True).",
    )
