"""Pydantic-схемы для эндпоинтов /server-accounts.

На write принимаем `password` опционально (если не задан — генерим серверной
стороной). В GET-карточке `password_b64` отдаётся только держателю action
`view_password`; для остальных поле остаётся `None`.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.password_policy import validate_password


class ServerAccountCreate(BaseModel):
    """Тело POST /server-accounts. Логин уникален в рамках сервера."""

    server_id: str = Field(..., description="FK на servers.id — сервер, для которого создаётся аккаунт.")
    # Regex держим в sync с `server_worker/src/clients/ssh.py::_LOGIN_RE` —
    # там идёт повторная валидация перед `chpasswd`, чтобы воркер не зависел
    # от того, дошёл ли request через эту схему. Если меняешь pattern —
    # меняй и там, и обнови тесты в обоих сервисах.
    login: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._\-]+$",
        description=(
            "Имя OS-аккаунта (root/postgres/...). "
            "Только буквы/цифры/`.`/`_`/`-` — защита от CRLF и shell-инъекций "
            "в audit details и в `chpasswd` payload."
        ),
    )
    password: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Plaintext пароля. Если не передан — сервер сгенерирует "
            "`secrets.token_urlsafe(32)`. При ручном вводе действует "
            "политика: минимум 8 символов, буквы и цифры."
        ),
    )
    has_sudo: bool = Field(
        default=False,
        description="Право sudo. Требует отдельного action `grant_sudo` (admin-only).",
    )
    unix_groups: list[str] = Field(
        default_factory=list, description="Список Unix-групп аккаунта (без проверки существования)."
    )
    linked_user_id: str | None = Field(
        default=None, description="Опциональный FK на платформенного user'а (для DBoS-аккаунтов)."
    )
    shell: str | None = Field(
        default=None, max_length=64, description="Login shell (/bin/bash и т.п.)."
    )
    home_dir: str | None = Field(
        default=None, max_length=256, description="Путь home-директории."
    )

    @field_validator("password")
    @classmethod
    def _check_password_policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_password(value)


class ServerAccountUpdate(BaseModel):
    """Тело PATCH /server-accounts/{id}. Пароль через `rotate_password`."""

    has_sudo: bool | None = Field(default=None, description="Сменить sudo-флаг (требует `grant_sudo`).")
    unix_groups: list[str] | None = Field(default=None, description="Перезаписать список групп.")
    linked_user_id: str | None = Field(default=None, description="Сменить связь с user'ом.")
    shell: str | None = Field(default=None, max_length=64, description="Сменить shell.")
    home_dir: str | None = Field(default=None, max_length=256, description="Сменить home_dir.")
    is_active: bool | None = Field(default=None, description="Отключить/включить аккаунт.")


class ServerAccountResponse(BaseModel):
    """Карточка аккаунта в ответе.

    `password_b64` заполняется только когда вызывающий держит action
    `view_password` — тогда это base64(plaintext). У вызывающего без
    `view_password` (только `view`) поле остаётся `None`. Сырого
    `password_encrypted` в ответе нет никогда.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Account ID (prefix acc_).")
    server_id: str = Field(description="FK на server.")
    login: str = Field(description="OS-логин.")
    has_sudo: bool = Field(description="Есть ли sudo.")
    unix_groups: list[str] = Field(description="Unix-группы.")
    linked_user_id: str | None = Field(default=None, description="FK на platform user.")
    shell: str | None = Field(default=None, description="Login shell.")
    home_dir: str | None = Field(default=None, description="Home directory.")
    is_active: bool = Field(description="Активен ли аккаунт.")
    password_rotated_at: datetime | None = Field(
        default=None, description="Когда последний раз ротировался пароль."
    )
    password_b64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded plaintext-пароль. Присутствует только если "
            "вызывающий держит action `view_password`; иначе `null`. "
            "Декодируется стандартным base64.b64decode перед использованием."
        ),
    )
    created_at: datetime = Field(description="Когда аккаунт создан.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
    created_by: str | None = Field(default=None, description="user_id, создавший аккаунт.")


class ServerAccountRotateRequest(BaseModel):
    """Тело POST /server-accounts/{id}/rotate_password.

    `password` опционален: если передан — проходит политику (минимум 8
    символов, буквы и цифры) и используется как новый пароль; если нет —
    сервер генерирует `secrets.token_urlsafe(32)`. Plaintext в ответ не
    возвращается ни в одном случае.
    """

    password: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Plaintext нового пароля. Если пуст — сервер сгенерирует "
            "случайный. При ручном вводе действует политика: минимум 8 "
            "символов, буквы и цифры."
        ),
    )

    @field_validator("password")
    @classmethod
    def _check_password_policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_password(value)


class ServerAccountRotateResponse(BaseModel):
    """Ответ на rotate_password — без plaintext'а наружу."""

    id: str = Field(description="Account ID.")
    login: str = Field(description="OS-логин.")
    rotated_at: datetime = Field(description="UTC timestamp ротации.")
