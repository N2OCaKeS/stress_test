"""Pydantic-схемы для /department-test-account.

Секреты наружу не отдаются никогда: ответ несёт логин, публичный ключ и
признак «пароль задан». Пароль принимается только на запись, приватный
ключ генерируется сервисом и живёт в secret_service.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Тот же POSIX-набор, что `server_worker/src/clients/ssh.py::_LOGIN_RE`
# (логин подставляется в useradd/chpasswd), плюс длина `test_username`.
LOGIN_PATTERN = r"^[A-Za-z_][A-Za-z0-9._-]{0,31}$"
# Шаблон домашнего каталога: абсолютный путь из безопасных символов,
# единственная подстановка — `{TEST_USER}`.
_HOME_TEMPLATE_RE = re.compile(r"^/[A-Za-z0-9._/{}-]*$")
_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")
_ALLOWED_PLACEHOLDERS = frozenset({"{TEST_USER}"})


class DepartmentTestAccountUpdate(BaseModel):
    """Тело PUT /department-test-account/{department_id}.

    Все поля опциональны: незаданное — «оставить как есть». На первой
    настройке пароль обязателен, логин по умолчанию берётся из подсказки
    (`department_test_settings.test_username`), SSH-пара генерируется всегда.
    """

    login: str | None = Field(
        default=None, pattern=LOGIN_PATTERN,
        description="Логин пользователя исполнения теста на стенде.",
    )
    password: str | None = Field(
        default=None, min_length=1, max_length=256,
        description="Новый пароль учётки. Не отдаётся обратно ни в каком виде.",
    )
    regenerate_ssh_key: bool = Field(
        default=False,
        description=(
            "Сгенерировать новую SSH-пару (Ed25519). На первой настройке пара "
            "генерируется независимо от флага."
        ),
    )
    home_template: str | None = Field(
        default=None, min_length=1, max_length=256,
        description="Шаблон домашнего каталога, `{TEST_USER}` — логин. По умолчанию `/home/{TEST_USER}`.",
    )

    @field_validator("password")
    @classmethod
    def _password_printable(cls, value: str | None) -> str | None:
        # Пароль уходит в `chpasswd` одной строкой `login:password\n` —
        # перевод строки или управляющий символ сломали бы формат.
        if value is not None and any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("password must not contain control characters or line breaks")
        return value

    @field_validator("home_template")
    @classmethod
    def _home_template_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _HOME_TEMPLATE_RE.fullmatch(value):
            raise ValueError("home_template must be an absolute path of [A-Za-z0-9._/-] and {TEST_USER}")
        unknown = set(_PLACEHOLDER_RE.findall(value)) - _ALLOWED_PLACEHOLDERS
        if unknown or value.count("{") != value.count("}"):
            raise ValueError("home_template supports only the {TEST_USER} placeholder")
        return value


class DepartmentTestAccountResponse(BaseModel):
    """Карточка тестовой учётки отдела — без пароля и приватного ключа."""

    department_id: str
    configured: bool = Field(description="Credential заведён и читается.")
    credential_id: str | None = Field(default=None, description="Ссылка на credential в secret_service.")
    credential_missing: bool = Field(
        default=False,
        description="Ссылка есть, но credential в secret_service не найден — учётку нужно задать заново.",
    )
    login: str | None = Field(default=None, description="Логин из credential.")
    login_hint: str = Field(description="Подсказка логина (легаси `test_username`, по умолчанию `u`).")
    has_password: bool = Field(default=False, description="Пароль задан.")
    ssh_public_key: str | None = Field(default=None, description="Публичная часть SSH-ключа (authorized_keys).")
    home_template: str = Field(description="Шаблон домашнего каталога.")
    home: str | None = Field(default=None, description="Шаблон, развёрнутый для текущего логина.")
    updated_at: datetime | None = Field(default=None)
